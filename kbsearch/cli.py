"""命令行：建库 / 增量增删 / 查询。

    python -m kbsearch build  <index_dir> <docs_dir> --stopwords <file>
    python -m kbsearch add    <index_dir> <doc_id> <file>
    python -m kbsearch delete <index_dir> <doc_id>
    python -m kbsearch query  <index_dir> <query text>
    python -m kbsearch batch  <index_dir> --queries <file> [--tsv]
"""

import argparse
import os
import sys

from .index import Index


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def cmd_build(args):
    docs_dir = args.docs_dir
    paths = []
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".txt"):
            continue
        paths.append(os.path.join(docs_dir, name))
    Index.create(args.index_dir, args.stopwords)
    with Index(args.index_dir).writer() as writer:
        batch = []
        for path in paths:
            doc_id = os.path.basename(path)[:-4]
            batch.append((doc_id, _read(path)))
        writer.add_documents(batch)
    print("indexed %d documents" % len(paths))


def cmd_add(args):
    with Index(args.index_dir).writer() as writer:
        writer.add_document(args.doc_id, _read(args.file))
    print("added %s" % args.doc_id)


def cmd_delete(args):
    with Index(args.index_dir).writer() as writer:
        ok = writer.delete_document(args.doc_id)
    print("deleted %s" % args.doc_id if ok else "not found: %s" % args.doc_id)
    return 0 if ok else 1


def cmd_query(args):
    with Index(args.index_dir).searcher() as searcher:
        results = searcher.search(args.query, limit=args.limit)
    for doc_id, score in results:
        print("%s\t%.6f" % (doc_id, score))


def cmd_batch(args):
    with open(args.queries, "r", encoding="utf-8") as fh:
        queries = [line.rstrip("\n") for line in fh if line.strip()]
    with Index(args.index_dir).searcher() as searcher:
        for query in queries:
            results = searcher.search(query, limit=args.limit)
            ids = ",".join(doc_id for doc_id, _ in results)
            if args.tsv:
                print("%s\t%s" % (query, ids))
            else:
                print("Q: %s" % query)
                print("   %s" % (ids if ids else "(no hits)"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kbsearch")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("index_dir")
    build.add_argument("docs_dir")
    build.add_argument("--stopwords", required=True)
    build.set_defaults(func=cmd_build)

    add = sub.add_parser("add")
    add.add_argument("index_dir")
    add.add_argument("doc_id")
    add.add_argument("file")
    add.set_defaults(func=cmd_add)

    delete = sub.add_parser("delete")
    delete.add_argument("index_dir")
    delete.add_argument("doc_id")
    delete.set_defaults(func=cmd_delete)

    query = sub.add_parser("query")
    query.add_argument("index_dir")
    query.add_argument("query")
    query.add_argument("--limit", type=int, default=None)
    query.set_defaults(func=cmd_query)

    batch = sub.add_parser("batch")
    batch.add_argument("index_dir")
    batch.add_argument("--queries", required=True)
    batch.add_argument("--tsv", action="store_true")
    batch.add_argument("--limit", type=int, default=None)
    batch.set_defaults(func=cmd_batch)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
