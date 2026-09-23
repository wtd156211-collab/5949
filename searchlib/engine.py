"""检索引擎：布尔求值 + BM25 打分。

- BM25 参数固定 k1 = 1.2、b = 0.75；
- idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))；
- 短语作为整体打分：tf 为连续命中次数，df 为命中短语的文档数，
  乘固定短语加权 2.0，短语里的词不再单独计分；
- 结果集合按布尔语义取交/并/补，得分是各正向操作数贡献之和，
  NOT 只影响集合、不参与打分；
- 排序：分数降序，浮点值完全相等时按文档编号升序（str 比较与
  UTF-8 字节序一致），结果稳定可复现。
"""

import math
import os

from .index import IndexReader
from .query import parse
from .tokenizer import load_stopwords

K1 = 1.2
B = 0.75
PHRASE_WEIGHT = 2.0


class SearchEngine:
    def __init__(self, index_dir):
        self._reader = IndexReader(index_dir)
        self._stopwords = load_stopwords(os.path.join(index_dir, "stopwords.txt"))

    def search(self, query, limit=None):
        """返回按相关度排序的文档编号列表。"""
        ast = parse(query, self._stopwords)
        if ast is None:
            return []
        matched, scores = self._eval(ast)
        ranked = sorted(matched, key=lambda d: (-scores.get(d, 0.0), d))
        if limit is not None:
            return ranked[:limit]
        return ranked

    def _idf(self, df):
        n = self._reader.num_docs
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, idf, tf, doc_len):
        avgdl = self._reader.avgdl
        denom = tf + K1 * (1 - B + B * doc_len / avgdl) if avgdl else tf + K1
        return idf * (tf * (K1 + 1)) / denom

    def _eval(self, node):
        """返回 (匹配文档集合, {doc_id: 正向得分贡献})。"""
        kind = node.kind
        if kind == "term":
            return self._eval_term(node.value)
        if kind == "phrase":
            return self._eval_phrase(node.value)
        if kind == "and":
            left_set, left_score = self._eval(node.children[0])
            right_set, right_score = self._eval(node.children[1])
            result = left_set & right_set
            scores = {
                d: left_score.get(d, 0.0) + right_score.get(d, 0.0) for d in result
            }
            return result, scores
        if kind == "or":
            left_set, left_score = self._eval(node.children[0])
            right_set, right_score = self._eval(node.children[1])
            result = left_set | right_set
            scores = {
                d: left_score.get(d, 0.0) + right_score.get(d, 0.0) for d in result
            }
            return result, scores
        if kind == "not":
            child_set, _ = self._eval(node.children[0])
            result = set(self._reader.all_doc_ids()) - child_set
            return result, {}
        raise ValueError("未知节点: %r" % kind)

    def _eval_term(self, term):
        df = self._reader.df(term)
        if df == 0:
            return set(), {}
        idf = self._idf(df)
        matched = set()
        scores = {}
        for doc_id, doc_len, positions in self._reader.postings(term):
            matched.add(doc_id)
            scores[doc_id] = self._bm25(idf, len(positions), doc_len)
        return matched, scores

    def _eval_phrase(self, terms):
        if not terms:
            return set(), {}
        if len(terms) == 1:
            return self._eval_term(terms[0])
        postings = []
        for term in terms:
            entries = self._reader.postings(term)
            if not entries:
                return set(), {}
            postings.append({doc_id: (doc_len, positions)
                             for doc_id, doc_len, positions in entries})
        common = set(postings[0])
        for entry in postings[1:]:
            common &= set(entry)
        matched = set()
        tf_by_doc = {}
        len_by_doc = {}
        for doc_id in common:
            doc_len, first_positions = postings[0][doc_id]
            count = 0
            for pos in first_positions:
                if all(pos + i in postings[i][doc_id][1]
                       for i in range(1, len(terms))):
                    count += 1
            if count:
                matched.add(doc_id)
                tf_by_doc[doc_id] = count
                len_by_doc[doc_id] = doc_len
        if not matched:
            return set(), {}
        idf = self._idf(len(matched))
        scores = {
            d: PHRASE_WEIGHT * self._bm25(idf, tf_by_doc[d], len_by_doc[d])
            for d in matched
        }
        return matched, scores
