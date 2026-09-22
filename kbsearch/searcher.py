"""查询求值与 BM25 打分。

内存策略：只把查询涉及的词项倒排表读进内存（按查询缓存），文档长度/外部编号
按需从 slots.bin、docs/ 随机读取；NOT 的全集是位图迭代，不物化大列表。

结果集按布尔语义取交并补；得分只累加正向操作数（TERM/PHRASE）贡献，NOT 不计分。
打分按 AST 从左到右的叶子顺序累加，排序以 (-score, 外部编号 UTF-8 字节序) 为键，
保证同一份数据重复运行结果逐字节一致。
"""

import math

from .index import _ReaderBase
from .parser import parse_query


class _BitmapView:
    """活文档全集：按需迭代/判存，不持有编号列表。"""

    def __init__(self, live):
        self.live = live

    def __contains__(self, doc):
        return self.live.get(doc)

    def __iter__(self):
        return self.live.ids()

    def materialize(self):
        return set(self.live.ids())


class _ComplementView:
    """all_docs 减去 inner。"""

    def __init__(self, live, inner):
        self.live = live
        self.inner = inner

    def __contains__(self, doc):
        return self.live.get(doc) and doc not in self.inner

    def __iter__(self):
        for doc in self.live.ids():
            if doc not in self.inner:
                yield doc

    def materialize(self):
        return set(self)


class NodeResult:
    """集合（可为视图）+ 正向叶子打分明细。

    parts 按 AST 叶子顺序排列：每个元素是 (docs, scores)，scores 为
    {doc: 贡献分} 或 None（无打分的纯布尔侧，如 NOT 子树）。
    """

    def __init__(self, docs, parts):
        self.docs = docs
        self.parts = parts


class Searcher(_ReaderBase):
    def search(self, query, limit=None):
        """返回 [(doc_id, score), ...]，已按排序规则排好。"""
        ast = parse_query(query, self.meta["stopwords"])
        if ast is None:
            return []
        self._postings_cache = {}  # 缓存仅在本次查询内有效
        result = self._eval_cached(ast)
        score_totals = {}
        for _docs, scores in result.parts:
            if scores is None:
                continue
            for doc, score in scores.items():
                score_totals[doc] = score_totals.get(doc, 0.0) + score
        scored = []
        for internal in result.docs:
            doc_id = self.external_id(internal)
            scored.append((doc_id.encode("utf-8"), score_totals.get(internal, 0.0)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        if limit is not None:
            scored = scored[:limit]
        return [(doc_id.decode("utf-8"), score) for doc_id, score in scored]

    # ---- 倒排表缓存：每个查询里一个词项只读一次盘 ---------------------------

    def _posting(self, term):
        cache = self._postings_cache
        if term not in cache:
            cache[term] = self.posting_for_term(term)
        return cache[term]

    # ---- AST 求值（每个查询内词项倒排表只读一次盘）--------------------------

    def _eval_cached(self, node):
        kind = node[0]
        if kind == "TERM":
            return self._eval_term(node[1])
        if kind == "PHRASE":
            return self._eval_phrase(node[1])
        if kind == "EMPTY":
            return NodeResult(set(), [])
        if kind == "NOT":
            inner = self._eval_cached(node[1])
            docs = _ComplementView(self._live, inner.docs)
            return NodeResult(docs, [])
        if kind == "OR":
            return self._eval_or(node[1])
        if kind == "AND":
            return self._eval_and(node[1])
        raise ValueError("未知查询节点：%r" % (node,))

    def _eval_term(self, term):
        postings = self._posting(term)
        docs = set(postings)
        scores = self._score_postings(term, postings, phrase=False)
        return NodeResult(docs, [(docs, scores)])

    def _eval_phrase(self, words):
        if not words:
            return NodeResult(set(), [])
        hits = self._phrase_hits(words)
        # 单词短语与多词短语一致：作为整体按命中次数打分，固定加权 2.0。
        scores = self._score_phrase(words, hits)
        return NodeResult(set(hits), [(set(hits), scores)])

    def _phrase_hits(self, words):
        """返回 {doc: 连续命中次数}，重叠命中也计数。"""
        first = self._posting(words[0])
        hits = {}
        for doc, positions in first.items():
            current = list(positions)
            ok = True
            for word in words[1:]:
                next_positions = set(self._posting(word).get(doc, ()))
                if not next_positions:
                    ok = False
                    break
                advanced = []
                for pos in current:
                    if pos + 1 in next_positions:
                        advanced.append(pos + 1)
                current = advanced
                if not current:
                    ok = False
                    break
            if ok and current:
                # 每个走完整个短语链的起点对应一次命中；链长 = 词数，
                # current 中的每个位置对应一个不同起点。
                hits[doc] = len(current)
        return hits

    # ---- 组合节点 -----------------------------------------------------------

    def _eval_or(self, children):
        results = [self._eval_cached(child) for child in children]
        docs = set()
        parts = []
        for result in results:
            docs |= set(result.docs)
            parts.extend(result.parts)
        return NodeResult(docs, parts)

    def _eval_and(self, children):
        results = [self._eval_cached(child) for child in children]
        # 选可枚举且最小的集合驱动交集；纯 NOT 侧（视图）用于判存。
        def driver_key(result):
            if isinstance(result.docs, (_BitmapView, _ComplementView)):
                return None
            return len(result.docs)

        driver_idx = None
        driver_size = None
        for idx, result in enumerate(results):
            size = driver_key(result)
            if size is None:
                continue
            if driver_size is None or size < driver_size:
                driver_size = size
                driver_idx = idx

        if driver_idx is None:
            # 全是 NOT（或全集视图）：驱动用活文档全集。
            docs = set(self._live.ids())
            for result in results:
                docs = {d for d in docs if d in result.docs}
        else:
            docs = set(results[driver_idx].docs)
            for idx, result in enumerate(results):
                if idx == driver_idx:
                    continue
                side = result.docs
                docs = {d for d in docs if d in side}

        parts = []
        for result in results:
            parts.extend(result.parts)
        return NodeResult(docs, parts)

    # ---- BM25 ---------------------------------------------------------------

    def _idf(self, df):
        n = self.n_docs
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def _score_postings(self, term, postings, phrase):
        df = len(postings)
        if df == 0:
            return {}
        idf = self._idf(df)
        avgdl = self.avgdl if self.avgdl > 0 else 1.0
        k1 = self.meta["k1"]
        b = self.meta["b"]
        weight = 2.0 if phrase else 1.0
        scores = {}
        for doc, positions in postings.items():
            tf = len(positions)
            dl = self.doc_length(doc)
            denom = tf + k1 * (1.0 - b + b * dl / avgdl)
            scores[doc] = weight * idf * (tf * (k1 + 1.0)) / denom
        return scores

    def _score_phrase(self, words, hits):
        """短语作为整体：tf=连续命中次数，df=命中文档数，固定加权 2.0。"""
        df = len(hits)
        if df == 0:
            return {}
        idf = self._idf(df)
        avgdl = self.avgdl if self.avgdl > 0 else 1.0
        k1 = self.meta["k1"]
        b = self.meta["b"]
        scores = {}
        for doc, tf in hits.items():
            dl = self.doc_length(doc)
            denom = tf + k1 * (1.0 - b + b * dl / avgdl)
            scores[doc] = 2.0 * idf * (tf * (k1 + 1.0)) / denom
        return scores
