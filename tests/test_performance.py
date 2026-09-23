import os
import random
import shutil
import tempfile
import time
import unittest

from searchlib import IndexWriter, SearchEngine, load_stopwords

ROOT = os.path.join(os.path.dirname(__file__), "..")
STOPWORDS_PATH = os.path.join(ROOT, "samples", "stopwords.txt")

WORDS = (
    "索引 检索 文档 查询 倒排 位置 短语 排序 得分 词频 长度 落盘 内存 增量 "
    "重建 删除 新增 扫描 知识库 相关度 分词 停用词 布尔 短语 加权 稳定"
).split()


class PerformanceTest(unittest.TestCase):
    """几万篇规模的构建与查询应在几秒内完成（这里用 2 万篇冒烟）。"""

    def test_build_and_query_speed(self):
        tmp = tempfile.mkdtemp()
        try:
            index_dir = os.path.join(tmp, "index")
            os.makedirs(index_dir)
            shutil.copyfile(STOPWORDS_PATH, os.path.join(index_dir, "stopwords.txt"))
            rng = random.Random(20260923)
            writer = IndexWriter(index_dir, load_stopwords(STOPWORDS_PATH))
            n_docs = 20000
            start = time.monotonic()
            for i in range(n_docs):
                text = "，".join(
                    "".join(rng.choice(WORDS) for _ in range(rng.randint(1, 4)))
                    for _ in range(rng.randint(3, 8))
                )
                writer.add_document("doc-%06d" % i, text)
            writer.commit()
            build_secs = time.monotonic() - start
            self.assertLess(build_secs, 30, "构建 %d 篇耗时 %.1fs" % (n_docs, build_secs))

            engine = SearchEngine(index_dir)
            start = time.monotonic()
            for _ in range(50):
                engine.search('索引 AND 检索 OR "倒排索引" AND NOT 扫描')
            query_secs = (time.monotonic() - start) / 50
            self.assertLess(query_secs, 1, "单次查询耗时 %.3fs" % query_secs)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
