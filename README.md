# 知识库检索库（倒排索引）

内部知识库现在几万篇文档，搜索的做法是每次把全部文档读一遍做包含判断，一次查询要等十几秒，同事已经不用了。这次想自己写一个索引和检索的库出来。

查询要支持与、或、非的组合，还要能查短语——几个词必须连着出现，顺序不能反，光靠单词倒排表做不出来，位置信息怎么存要先想清楚。结果按相关度排序，分数一样的按文档编号排，顺序必须稳定，同一份数据跑两遍要一模一样。索引要落盘，查询的时候内存别跟着文档数涨：几万篇塞进内存没问题，几十万篇就塞不下，要的是后者也能用。新增和删除文档之后不能每次都全量重建，得有办法只动受影响的那部分。性能上，几万篇建索引和查一次都要在几秒内完成，别一查就全表扫。实现只用标准库，测试用 unittest。

仓库里只有这份说明和 samples/ 下的样例文档、样例查询与期望结果，代码从零写。

## 分词口径

文档和查询用完全相同的分词口径，顺序是「切分 → 转小写 → 丢标点 → 过滤停用词 → 连续编号」：

1. **切分**：连续的 ASCII 字母或数字算一个词元（例如 `BM25`、`doc01`）；每个中日韩汉字单独成词元；其余字符（标点、空白、符号、全角字母等）直接丢弃，一个词元也不产生。
2. **大小写**：词元一律转成小写（只处理 ASCII）。
3. **标点**：不作为词元，丢弃之后**不占位置**。
4. **停用词**：停用词表见 samples/stopwords.txt（一行一个词，`#` 开头是注释）；命中停用词的词元丢弃，同样不占位置。
5. **位置编号**：文档里保留下来的词元按出现顺序编号，从 1 开始，这个编号就是后面短语匹配用的位置。因为标点和停用词不占位置，`state of the art` 这种带停用词的说法，位置仍然是连续的。

## 查询语法

| 写法 | 含义 |
|---|---|
| `检索` | 单词查询：操作数也按同一口径切词，切出多个词元时按隐式「与」组合；中文切成单字，所以 `倒排索引` 展开成「倒 与 排 与 索 与 引」 |
| `a b` | 相邻两个操作数之间没有运算符，按「与」处理（隐式 AND） |
| `a AND b` | 与 |
| `a OR b` | 或 |
| `NOT a`、`a AND NOT b` | 非 |
| `(a OR b) AND c` | 括号改变结合顺序 |
| `"state of the art"` | 短语：按同一分词口径切词后，各词的位置必须连续且顺序一致 |

优先级：`NOT` > `AND`（含隐式 AND）> `OR`。查询串也用同一套分词口径，因此查询里的标点和停用词会被丢掉：`"the state of the art"` 与 `"state of the art"` 等价。切完变成空的查询（例如只查一个停用词）匹配不到任何文档。中文要按词连着匹配时用双引号写短语，例如 `"倒排索引"`；不写引号的 `倒排索引` 是四个单字的与。

## 打分公式

BM25，参数固定为 `k1 = 1.2`、`b = 0.75`，不额外做平滑之外的处理。

- 词频 `tf(t, d)`：词 `t` 在文档 `d` 里出现的次数；
- 文档长度 `|d|`：文档分词后的词元数；`avgdl` 是全部文档的平均长度；`N` 是文档总数；
- `idf(t) = ln(1 + (N − df(t) + 0.5) / (df(t) + 0.5))`，其中 `df(t)` 是包含 `t` 的文档数；
- 单词贡献：`idf(t) × (tf × (k1 + 1)) / (tf + k1 × (1 − b + b × |d| / avgdl))`；
- **短语**作为一个整体参与打分：`tf` 取该短语在文档中连续命中的次数，`df` 取命中过该短语的文档数，`idf` 用同一个公式，并乘上固定的短语加权 `2.0`；短语里的词不再单独计一次分；
- **多条件组合**：结果集合按布尔语义取（AND 取交集、OR 取并集、NOT 取补集），得分是各正向操作数贡献之和；`NOT` 只影响集合，不参与打分。

排序：分数降序；分数完全相同（浮点值相等）时按文档编号升序。文档编号就是文件名去掉 `.txt`，比较用 UTF-8 字节序。

## 样例

- `samples/docs/doc-01.txt` ~ `doc-10.txt`：10 篇样例文档；
- `samples/queries.txt`：一行一个查询，覆盖单词、隐式 AND、显式 AND、OR、NOT、括号、短语、停用词短语和查不到的情况；
- `samples/expected.tsv`：一行一个查询，格式是「查询 + TAB + 按顺序排列的文档编号（逗号分隔）」，没有命中时 TAB 后面为空。期望结果只给排序，不给分数，避免浮点格式带来的差异。

期望结果就是验收标准：同一份文档和查询，排出来的文档顺序必须与 `samples/expected.tsv` 完全一致。

## 索引文件布局

索引是一个目录，全部用 JSON（UTF-8、`sort_keys`）落盘，写入先写临时文件再 `os.replace`，避免半截文件：

```
index_dir/
    meta.json          {"num_docs": 文档总数 N, "total_len": 全部文档词元数之和}
                       avgdl = total_len / num_docs，查询时现算
    docs.json          {doc_id: 文档词元数}，NOT 查询的全集也取自这里
    terms.json         {term: df}，词表大小，查询时常驻内存
    stopwords.txt      建索引时拷贝的停用词表，保证查询与建索引口径一致
    postings/xx.json   256 个 shard，xx 是 term 的 sha1 首字节（十六进制）：
                       {term: [[doc_id, doc_len, [pos, ...]], ...]}
                       doc_len 冗余在每条倒排记录里，打分时不需要加载
                       任何随文档数增长的结构；pos 从 1 开始连续编号
    forward/xx.json    256 个 shard，xx 是 doc_id 的 sha1 首字节：
                       {doc_id: {term: [pos, ...]}}，删除文档时靠它找到
                       该文档的所有词
```

查询时内存占用：`terms.json`（词表大小）+ 查询词命中的 postings shard + 结果集，都不随文档总数增长；只有 `NOT` 需要全集时会读 `docs.json`。

增量更新：新增/删除一篇文档只重写它命中的 postings shard（该文档不同的词数级别）和它所在的 forward shard，再更新 `terms.json`、`docs.json`、`meta.json`，不做全量重建。删除时从 forward shard 拿到该文档的词表，逐个从对应 postings shard 里摘掉该文档并递减 df。

## 代码结构

- `searchlib/tokenizer.py`：分词（切分、小写、丢标点、停用词、位置编号）
- `searchlib/query.py`：查询词法分析与递归下降解析（NOT > AND > OR，隐式 AND，括号，双引号短语）
- `searchlib/index.py`：`IndexWriter`（构建/增量增删/commit 落盘）与 `IndexReader`（按需加载 shard）
- `searchlib/engine.py`：`SearchEngine.search()`，布尔求值 + BM25 打分 + 稳定排序
- `main.py`：命令行入口，见下
- `tests/`：unittest 测试，`python3 -m unittest discover -s tests`

## 命令行用法

```
python3 main.py build  --index IDX --docs samples/docs --stopwords samples/stopwords.txt
python3 main.py add    --index IDX path/to/new-doc.txt
python3 main.py delete --index IDX doc-01
python3 main.py search --index IDX '(倒排索引 OR 扫描) AND 文档'
```
