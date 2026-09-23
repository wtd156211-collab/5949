"""命令行入口：

    python main.py build  --index IDX --docs DIR --stopwords FILE
    python main.py add    --index IDX FILE [FILE ...]
    python main.py delete --index IDX DOC_ID [DOC_ID ...]
    python main.py search --index IDX "查询" [--limit N]

文档编号 = 文件名去掉 .txt。
"""

import argparse
import os
import shutil
import sys

from searchlib import IndexWriter, SearchEngine, load_stopwords


def _doc_id(path):
    name = os.path.basename(path)
    if name.endswith(".txt"):
        name = name[: -len(".txt")]
    return name


def cmd_build(args):
    if os.path.exists(args.index):
        shutil.rmtree(args.index)
    os.makedirs(args.index)
    shutil.copyfile(args.stopwords, os.path.join(args.index, "stopwords.txt"))
    writer = IndexWriter(args.index, load_stopwords(args.stopwords))
    files = sorted(
        f for f in os.listdir(args.docs) if f.endswith(".txt")
    )
    for name in files:
        path = os.path.join(args.docs, name)
        with open(path, "r", encoding="utf-8") as f:
            writer.add_document(_doc_id(name), f.read())
    writer.commit()
    print("已索引 %d 篇文档 -> %s" % (len(files), args.index))


def _open_writer(index_dir):
    return IndexWriter(index_dir, load_stopwords(os.path.join(index_dir, "stopwords.txt")))


def cmd_add(args):
    writer = _open_writer(args.index)
    for path in args.files:
        with open(path, "r", encoding="utf-8") as f:
            writer.add_document(_doc_id(path), f.read())
    writer.commit()
    print("已新增 %d 篇文档" % len(args.files))


def cmd_delete(args):
    writer = _open_writer(args.index)
    for doc_id in args.doc_ids:
        writer.delete_document(doc_id)
    writer.commit()
    print("已删除 %d 篇文档" % len(args.doc_ids))


def cmd_search(args):
    engine = SearchEngine(args.index)
    for doc_id in engine.search(args.query, limit=args.limit):
        print(doc_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="知识库检索库")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="全量构建索引")
    p.add_argument("--index", required=True)
    p.add_argument("--docs", required=True)
    p.add_argument("--stopwords", required=True)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("add", help="增量新增文档")
    p.add_argument("--index", required=True)
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("delete", help="增量删除文档")
    p.add_argument("--index", required=True)
    p.add_argument("doc_ids", nargs="+")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search", help="查询")
    p.add_argument("--index", required=True)
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
