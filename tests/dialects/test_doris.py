from sqlglot import ErrorLevel, ParseError, UnsupportedError, exp, parse_one
from tests.dialects.test_dialect import Validator


class TestDoris(Validator):
    dialect = "doris"

    def test_aggregate_filter(self):
        for function in ("SUM", "AVG", "MIN", "MAX", "COUNT", "ANY_VALUE"):
            for argument in ("a", "DISTINCT a"):
                conditional = "IF(p, a, NULL)"
                if argument.startswith("DISTINCT"):
                    conditional = f"DISTINCT {conditional}"
                source = f"SELECT {function}({argument}) FILTER(WHERE p) AS v FROM t"
                expected = f"SELECT {function}({conditional}) AS v FROM t"
                with self.subTest(source):
                    self.validate_all(
                        expected, read={"postgres": source}, write={"doris": expected}
                    )
                    tree = parse_one(source, read="postgres")
                    original = tree.copy()
                    self.assertEqual(
                        tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                    )
                    self.assertEqual(tree, original)
                    self.validate_identity(expected)

        for source, expected in (
            ("SELECT COUNT(*) FILTER(WHERE p) FROM t", "SELECT COUNT(IF(p, 1, NULL)) FROM t"),
            ("SELECT COUNT(*) FILTER(WHERE NULL) FROM t", "SELECT COUNT(IF(NULL, 1, NULL)) FROM t"),
            (
                "SELECT COUNT(*) FILTER(WHERE TRUE) FROM t WHERE FALSE",
                "SELECT COUNT(IF(TRUE, 1, NULL)) FROM t WHERE FALSE",
            ),
            ("SELECT SUM(a) FILTER(WHERE FALSE) FROM t", "SELECT SUM(IF(FALSE, a, NULL)) FROM t"),
            (
                "SELECT SUM(a) FILTER(WHERE p) FROM t HAVING COUNT(*) FILTER(WHERE p)>0",
                "SELECT SUM(IF(p, a, NULL)) FROM t HAVING COUNT(IF(p, 1, NULL)) > 0",
            ),
            (
                "SELECT g, MIN(a) FILTER(WHERE p) FROM t GROUP BY g ORDER BY g",
                "SELECT g, MIN(IF(p, a, NULL)) FROM t GROUP BY g ORDER BY g NULLS LAST",
            ),
            (
                "SELECT (SELECT MIN(a) FILTER(WHERE p) FROM t) AS v",
                "SELECT (SELECT MIN(IF(p, a, NULL)) FROM t) AS v",
            ),
        ):
            with self.subTest(source):
                self.validate_all(expected, read={"postgres": source}, write={"doris": expected})
                self.validate_identity(expected)

    def test_aggregate_filter_exclusions(self):
        for source, expected in (
            (
                "SELECT COUNT(t.*) FILTER(WHERE p) FROM t",
                "SELECT COUNT(t.*) FILTER(WHERE p) FROM t",
            ),
            (
                "SELECT ARRAY_AGG(a) FILTER(WHERE p) FROM t",
                "SELECT COLLECT_LIST(a) FILTER(WHERE p) FROM t",
            ),
            ("SELECT my_agg(a) FILTER(WHERE p) FROM t", "SELECT MY_AGG(a) FILTER(WHERE p) FROM t"),
            (
                "SELECT SUM(a) FILTER(WHERE p) OVER () FROM t",
                "SELECT SUM(a) FILTER(WHERE p) OVER () FROM t",
            ),
            (
                "SELECT SUM(a ORDER BY b) FILTER(WHERE p) FROM t",
                "SELECT SUM(a ORDER BY b NULLS LAST) FILTER(WHERE p) FROM t",
            ),
        ):
            with self.subTest(source):
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )

    def test_array_agg_nulls_and_empty_input(self):
        for source, expected in (
            ("SELECT ARRAY_AGG(a) FROM t", "SELECT NULLIF(ARRAY_AGG(a), ARRAY()) FROM t"),
            (
                "SELECT ARRAY_AGG(DISTINCT a) FROM t",
                "SELECT NULLIF(ARRAY_AGG(DISTINCT a), ARRAY()) FROM t",
            ),
            (
                "SELECT ARRAY_AGG(CAST(a AS BIGINT)) AS xs FROM t WHERE FALSE",
                "SELECT NULLIF(ARRAY_AGG(CAST(a AS BIGINT)), ARRAY()) AS xs FROM t WHERE FALSE",
            ),
            (
                "SELECT g, ARRAY_AGG(a) AS xs, ARRAY_AGG(b) AS ys FROM t GROUP BY g",
                "SELECT g, NULLIF(ARRAY_AGG(a), ARRAY()) AS xs, NULLIF(ARRAY_AGG(b), ARRAY()) AS ys FROM t GROUP BY g",
            ),
            (
                "SELECT g FROM t GROUP BY g HAVING ARRAY_AGG(a) IS NULL",
                "SELECT g FROM t GROUP BY g HAVING NULLIF(ARRAY_AGG(a), ARRAY()) IS NULL",
            ),
            (
                "SELECT (SELECT ARRAY_AGG(a) FROM t WHERE FALSE) AS xs",
                "SELECT (SELECT NULLIF(ARRAY_AGG(a), ARRAY()) FROM t WHERE FALSE) AS xs",
            ),
        ):
            with self.subTest(source):
                self.validate_all(expected, read={"postgres": source}, write={"doris": expected})
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )
                self.validate_identity(expected)

        self.validate_identity("SELECT ARRAY_AGG(a) FROM t")
        self.validate_identity("SELECT COLLECT_LIST(a) FROM t")
        self.validate_all(
            "SELECT COLLECT_LIST(a) FROM t",
            read={
                "hive": "SELECT COLLECT_LIST(a) FROM t",
                "spark": "SELECT COLLECT_LIST(a) FROM t",
            },
        )

    def test_array_agg_excluded_contexts(self):
        for source, expected in (
            (
                "SELECT ARRAY_AGG(a ORDER BY b) FROM t",
                "SELECT COLLECT_LIST(a ORDER BY b NULLS LAST) FROM t",
            ),
            ("SELECT ARRAY_AGG(a) OVER () FROM t", "SELECT COLLECT_LIST(a) OVER () FROM t"),
            (
                "SELECT ARRAY_AGG(a) FILTER(WHERE p) FROM t",
                "SELECT COLLECT_LIST(a) FILTER(WHERE p) FROM t",
            ),
        ):
            with self.subTest(source):
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )

    def test_array_agg_value_ordering(self):
        for distinct in ("", "DISTINCT "):
            aggregate = f"ARRAY_AGG({distinct}a)"
            nonnull_aggregate = f"COLLECT_LIST({distinct}a)"
            for order, sorted_array in (
                ("a", f"ARRAY_SORT({nonnull_aggregate})"),
                ("a ASC NULLS FIRST", f"ARRAY_SORT({nonnull_aggregate})"),
                ("a ASC NULLS LAST", f"ARRAY_SORT({nonnull_aggregate})"),
                ("a DESC", f"ARRAY_REVERSE_SORT({nonnull_aggregate})"),
                ("a DESC NULLS FIRST", f"ARRAY_REVERSE_SORT({nonnull_aggregate})"),
                ("a DESC NULLS LAST", f"ARRAY_REVERSE_SORT({nonnull_aggregate})"),
            ):
                nulls_first = order in ("a ASC NULLS FIRST", "a DESC", "a DESC NULLS FIRST")
                nulls = f"ARRAY_SLICE(ARRAY_SORT({aggregate}), 1, ARRAY_SIZE({aggregate}) - ARRAY_SIZE({nonnull_aggregate}))"
                parts = f"{nulls}, {sorted_array}" if nulls_first else f"{sorted_array}, {nulls}"
                expected = f"SELECT NULLIF(ARRAY_CONCAT({parts}), ARRAY()) AS xs FROM t"
                source = f"SELECT ARRAY_AGG({distinct}a ORDER BY {order}) AS xs FROM t"
                with self.subTest(source):
                    self.validate_all(
                        expected, read={"postgres": source}, write={"doris": expected}
                    )
                    tree = parse_one(source, read="postgres")
                    original = tree.copy()
                    self.assertEqual(
                        tree.sql("doris", unsupported_level=ErrorLevel.RAISE), expected
                    )
                    self.assertEqual(tree, original)
                    self.validate_identity(expected)

        expected = (
            "SELECT NULLIF(ARRAY_CONCAT("
            "ARRAY_SORT(COLLECT_LIST(CAST(a AS BIGINT))), "
            "ARRAY_SLICE(ARRAY_SORT(ARRAY_AGG(CAST(a AS BIGINT))), 1, "
            "ARRAY_SIZE(ARRAY_AGG(CAST(a AS BIGINT))) - "
            "ARRAY_SIZE(COLLECT_LIST(CAST(a AS BIGINT))))), ARRAY()) FROM t"
        )
        self.validate_all(
            expected,
            read={
                "postgres": "SELECT ARRAY_AGG(CAST(a AS BIGINT) ORDER BY CAST(a AS BIGINT)) FROM t"
            },
        )

    def test_array_agg_value_ordering_contexts(self):
        self.validate_identity("SELECT ARRAY_SIZE(a) FROM t")
        aggregate = (
            "NULLIF(ARRAY_CONCAT(ARRAY_SORT(COLLECT_LIST(a)), "
            "ARRAY_SLICE(ARRAY_SORT(ARRAY_AGG(a)), 1, "
            "ARRAY_SIZE(ARRAY_AGG(a)) - ARRAY_SIZE(COLLECT_LIST(a)))), ARRAY())"
        )
        for source, expected in (
            (
                "SELECT ARRAY_AGG(a ORDER BY a) AS xs FROM t WHERE FALSE HAVING ARRAY_AGG(a ORDER BY a) IS NULL",
                f"SELECT {aggregate} AS xs FROM t WHERE FALSE HAVING {aggregate} IS NULL",
            ),
            (
                "SELECT g, ARRAY_AGG(a ORDER BY a) AS xs FROM t GROUP BY g ORDER BY g",
                f"SELECT g, {aggregate} AS xs FROM t GROUP BY g ORDER BY g NULLS LAST",
            ),
            (
                "SELECT (SELECT ARRAY_AGG(a ORDER BY a) FROM t WHERE FALSE) AS xs",
                f"SELECT (SELECT {aggregate} FROM t WHERE FALSE) AS xs",
            ),
        ):
            with self.subTest(source):
                self.validate_all(expected, read={"postgres": source}, write={"doris": expected})
                self.validate_identity(expected)

    def test_array_agg_value_ordering_exclusions(self):
        for source, expected in (
            (
                "SELECT ARRAY_AGG(a ORDER BY a, b) FROM t",
                "SELECT COLLECT_LIST(a ORDER BY a NULLS LAST, b NULLS LAST) FROM t",
            ),
            (
                'SELECT ARRAY_AGG(a ORDER BY a COLLATE "C") FROM t',
                "SELECT COLLECT_LIST(a ORDER BY a COLLATE `C` NULLS LAST) FROM t",
            ),
            (
                "SELECT ARRAY_AGG(RANDOM() ORDER BY RANDOM()) FROM t",
                "SELECT COLLECT_LIST(RAND() ORDER BY RAND() NULLS LAST) FROM t",
            ),
            (
                "SELECT ARRAY_AGG(a ORDER BY a) FILTER(WHERE p) FROM t",
                "SELECT COLLECT_LIST(a ORDER BY a NULLS LAST) FILTER(WHERE p) FROM t",
            ),
            (
                "SELECT ARRAY_AGG(a ORDER BY a) OVER () FROM t",
                "SELECT COLLECT_LIST(a ORDER BY a NULLS LAST) OVER () FROM t",
            ),
        ):
            with self.subTest(source):
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )

    def test_null_ordering(self):
        for source, expected in (
            ("SELECT a FROM t ORDER BY 1", "SELECT a FROM t ORDER BY 1 NULLS LAST"),
            ("SELECT a FROM t ORDER BY 1 DESC", "SELECT a FROM t ORDER BY 1 DESC NULLS FIRST"),
            ("SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY a NULLS LAST"),
            ("SELECT a FROM t ORDER BY a DESC", "SELECT a FROM t ORDER BY a DESC NULLS FIRST"),
            ("SELECT a FROM t ORDER BY a ASC NULLS FIRST", "SELECT a FROM t ORDER BY a ASC"),
            ("SELECT a FROM t ORDER BY a DESC NULLS LAST", "SELECT a FROM t ORDER BY a DESC"),
            (
                "SELECT a FROM t ORDER BY a ASC NULLS LAST",
                "SELECT a FROM t ORDER BY a ASC NULLS LAST",
            ),
            (
                "SELECT a FROM t ORDER BY a DESC NULLS FIRST",
                "SELECT a FROM t ORDER BY a DESC NULLS FIRST",
            ),
            (
                "SELECT a + 1 AS b FROM t ORDER BY b",
                "SELECT a + 1 AS b FROM t ORDER BY b NULLS LAST",
            ),
            (
                "SELECT a, b FROM t ORDER BY 1, 2 DESC",
                "SELECT a, b FROM t ORDER BY 1 NULLS LAST, 2 DESC NULLS FIRST",
            ),
            (
                "SELECT ROW_NUMBER() OVER (ORDER BY a) FROM t",
                "SELECT ROW_NUMBER() OVER (ORDER BY a NULLS LAST) FROM t",
            ),
            (
                "SELECT SUM(a) OVER (ORDER BY a ROWS UNBOUNDED PRECEDING) FROM t",
                "SELECT SUM(a) OVER (ORDER BY a NULLS LAST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
            ),
            (
                "SELECT SUM(a) OVER (ORDER BY a RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
                "SELECT SUM(a) OVER (ORDER BY a NULLS LAST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t",
            ),
        ):
            with self.subTest(source):
                self.validate_all(expected, read={"postgres": source}, write={"doris": expected})
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )

        self.validate_all(
            "SELECT a FROM t ORDER BY a NULLS LAST",
            write={"mysql": "SELECT a FROM t ORDER BY CASE WHEN a IS NULL THEN 1 ELSE 0 END, a"},
        )
        with self.assertRaisesRegex(UnsupportedError, "positional ordering"):
            parse_one("SELECT a FROM t ORDER BY 1", read="postgres").sql(
                "mysql", unsupported_level=ErrorLevel.RAISE
            )

    def test_array_constructor(self):
        for source, expected in (
            ("SELECT ARRAY[1, 2]", "SELECT ARRAY(1, 2)"),
            ("SELECT ARRAY[1, NULL, 2, 1]", "SELECT ARRAY(1, NULL, 2, 1)"),
            ("SELECT ARRAY[NULL]", "SELECT ARRAY(NULL)"),
            ("SELECT ARRAY[]::INT[]", "SELECT CAST(ARRAY() AS ARRAY<INT>)"),
            (
                "SELECT ARRAY[[1, 2], [3, 4]]",
                "SELECT ARRAY(ARRAY(1, 2), ARRAY(3, 4))",
            ),
            (
                "SELECT ARRAY[1::BIGINT, NULL::BIGINT]",
                "SELECT ARRAY(CAST(1 AS BIGINT), CAST(NULL AS BIGINT))",
            ),
            (
                "SELECT ARRAY[1.25::DECIMAL(10, 2), NULL::DECIMAL(10, 2)]",
                "SELECT ARRAY(CAST(1.25 AS DECIMAL(10, 2)), CAST(NULL AS DECIMAL(10, 2)))",
            ),
            (
                "SELECT ARRAY['hello', 'world', '']",
                "SELECT ARRAY('hello', 'world', '')",
            ),
            ("SELECT ARRAY[x, NULL] FROM t", "SELECT ARRAY(x, NULL) FROM t"),
            ("SELECT ARRAY[(SELECT 1)]", "SELECT ARRAY((SELECT 1))"),
            (
                "SELECT ARRAY[(SELECT 1 WHERE FALSE)]",
                "SELECT ARRAY((SELECT 1 WHERE FALSE))",
            ),
            (
                "SELECT ARRAY[(SELECT 1), (SELECT 2)]",
                "SELECT ARRAY((SELECT 1), (SELECT 2))",
            ),
            (
                "SELECT any_value(v) FROM (VALUES (ARRAY['hello', 'world'])) AS v(v)",
                "SELECT ANY_VALUE(v) FROM (SELECT ARRAY('hello', 'world') AS v) AS v",
            ),
        ):
            with self.subTest(source):
                self.assertEqual(
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )
                self.validate_identity(expected)
                self.assertEqual(
                    parse_one(expected, read="doris").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    expected,
                )

        for dialect, source in (
            ("hive", "SELECT ARRAY(1, 2)"),
            ("bigquery", "SELECT [1, 2]"),
            ("clickhouse", "SELECT [1, 2]"),
        ):
            with self.subTest(dialect):
                self.assertEqual(
                    parse_one(source, read=dialect).sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    ),
                    "SELECT ARRAY(1, 2)",
                )

        with self.assertRaises(UnsupportedError):
            parse_one("SELECT ARRAY[1, 2]", read="postgres").sql(
                "mysql", unsupported_level=ErrorLevel.RAISE
            )

    def test_array_query_constructor(self):
        for source in (
            "SELECT ARRAY(SELECT 1)",
            "SELECT ARRAY((SELECT 1))",
            "SELECT ARRAY(SELECT 1 UNION ALL SELECT 2)",
        ):
            with self.subTest(source):
                with self.assertRaisesRegex(UnsupportedError, "ARRAY subquery constructors"):
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

    def test_doris(self):
        self.validate_all(
            "SELECT TO_DATE('2020-02-02 00:00:00')",
            write={
                "doris": "SELECT TO_DATE('2020-02-02 00:00:00')",
                "oracle": "SELECT CAST('2020-02-02 00:00:00' AS DATE)",
            },
        )
        self.validate_all(
            "SELECT MAX_BY(a, b), MIN_BY(c, d)",
            read={
                "clickhouse": "SELECT argMax(a, b), argMin(c, d)",
            },
        )
        self.validate_all(
            "SELECT ARRAY_SUM(x -> x * x, ARRAY(2, 3))",
            read={
                "clickhouse": "SELECT arraySum(x -> x*x, [2, 3])",
            },
            write={
                "clickhouse": "SELECT arraySum(x -> x * x, [2, 3])",
                "doris": "SELECT ARRAY_SUM(x -> x * x, ARRAY(2, 3))",
            },
        )
        self.validate_all(
            "MONTHS_ADD(d, n)",
            read={
                "oracle": "ADD_MONTHS(d, n)",
            },
            write={
                "doris": "MONTHS_ADD(d, n)",
                "oracle": "ADD_MONTHS(d, n)",
            },
        )
        self.validate_all(
            """SELECT JSON_EXTRACT(CAST('{"key": 1}' AS JSONB), '$.key')""",
            read={
                "postgres": """SELECT '{"key": 1}'::jsonb ->> 'key'""",
            },
            write={
                "doris": """SELECT JSON_EXTRACT(CAST('{"key": 1}' AS JSONB), '$.key')""",
                "postgres": """SELECT JSON_EXTRACT_PATH(CAST('{"key": 1}' AS JSONB), 'key')""",
            },
        )
        self.validate_all(
            "SELECT GROUP_CONCAT('aa', ',')",
            read={
                "doris": "SELECT GROUP_CONCAT('aa', ',')",
                "mysql": "SELECT GROUP_CONCAT('aa' SEPARATOR ',')",
                "postgres": "SELECT STRING_AGG('aa', ',')",
            },
        )
        self.validate_all(
            "SELECT LAG(1, 1, NULL) OVER (ORDER BY 1 NULLS LAST)",
            read={
                "doris": "SELECT LAG(1, 1, NULL) OVER (ORDER BY 1 NULLS LAST)",
                "postgres": "SELECT LAG(1) OVER (ORDER BY 1)",
            },
        )
        self.validate_all(
            "SELECT LAG(1, 2, NULL) OVER (ORDER BY 1 NULLS LAST)",
            read={
                "doris": "SELECT LAG(1, 2, NULL) OVER (ORDER BY 1 NULLS LAST)",
                "postgres": "SELECT LAG(1, 2) OVER (ORDER BY 1)",
            },
        )
        self.validate_all(
            "SELECT LEAD(1, 1, NULL) OVER (ORDER BY 1 NULLS LAST)",
            read={
                "doris": "SELECT LEAD(1, 1, NULL) OVER (ORDER BY 1 NULLS LAST)",
                "postgres": "SELECT LEAD(1) OVER (ORDER BY 1)",
            },
        )
        self.validate_all(
            "SELECT LEAD(1, 2, NULL) OVER (ORDER BY 1 NULLS LAST)",
            read={
                "doris": "SELECT LEAD(1, 2, NULL) OVER (ORDER BY 1 NULLS LAST)",
                "postgres": "SELECT LEAD(1, 2) OVER (ORDER BY 1)",
            },
        )
        for function in ("LAG", "LEAD"):
            for offset in (1, 2):
                self.validate_identity(f"SELECT {function}(1, {offset}, NULL) OVER (ORDER BY 1)")
        self.validate_identity("""JSON_TYPE('{"foo": "1" }', '$.foo')""")

        self.validate_identity("L2_DISTANCE(x, y)")

    def test_identity(self):
        self.validate_identity("CREATE TABLE t (c INT) PROPERTIES ('x'='y')")
        self.validate_identity("CREATE TABLE t (c INT) COMMENT 'c'")
        self.validate_identity("COALECSE(a, b, c, d)")
        self.validate_identity("DROP TABLE my_table")
        self.validate_identity("DROP TABLE IF EXISTS example_db.my_table")
        self.validate_identity("DROP TABLE my_table FORCE")
        self.validate_identity("SELECT CAST(`a`.`b` AS INT) FROM foo")

        # Doris 4.1 reserves these (grammar: DorisLexer.g4 keywords minus
        # DorisParser.g4 nonReserved; each verified against a live 4.1 FE —
        # bare use is a parse error) but they were missing from
        # RESERVED_KEYWORDS, so identifiers with these names rendered unquoted
        for keyword in (
            "analyzer",
            "asof",
            "both",
            "char_filter",
            "dump",
            "layout",
            "leading",
            "match_condition",
            "no_use_mv",
            "play",
            "token_filter",
            "tokenizer",
            "trailing",
            "try_cast",
            "use_mv",
        ):
            with self.subTest(f"Doris reserved keyword: {keyword}"):
                self.validate_identity(f"SELECT `{keyword}` FROM t")
                self.assertEqual(
                    exp.select(keyword).from_("t").sql(dialect="doris"),
                    f"SELECT `{keyword}` FROM t",
                )
        self.validate_identity("SELECT APPROX_COUNT_DISTINCT(a) FROM x")
        self.validate_identity(
            "CREATE TABLE IF NOT EXISTS example_tbl_unique (user_id BIGINT NOT NULL, user_name VARCHAR(50) NOT NULL, city VARCHAR(20), age SMALLINT, sex TINYINT) UNIQUE KEY (user_id, user_name) DISTRIBUTED BY HASH (user_id) BUCKETS 10 PROPERTIES ('enable_unique_key_merge_on_write'='true')"
        )
        self.validate_identity("INSERT OVERWRITE TABLE test PARTITION(p1, p2) VALUES (1, 2)")

    def test_time(self):
        self.validate_identity("TIMESTAMP('2022-01-01')")
        self.validate_identity("DATE_TRUNC(event_date, 'DAY')")
        self.validate_identity("DATE_TRUNC('2010-12-02 19:28:30', 'HOUR')")
        self.validate_identity("CURRENT_DATE()")

    def test_date_add_sub(self):
        # Standard INTERVAL form
        self.validate_identity("SELECT DATE_ADD(x, INTERVAL '3' DAY)")
        self.validate_identity("SELECT DATE_SUB(x, INTERVAL '3' MONTH)")

        # Two-argument shorthand - default unit DAY
        self.validate_identity("SELECT DATE_SUB(x, 3)", "SELECT DATE_SUB(x, INTERVAL 3 DAY)")
        self.validate_identity("SELECT DATE_ADD(x, 7)", "SELECT DATE_ADD(x, INTERVAL 7 DAY)")

        # ADDDATE / SUBDATE aliases
        self.validate_identity("SELECT ADDDATE(x, 7)", "SELECT DATE_ADD(x, INTERVAL 7 DAY)")
        self.validate_identity("SELECT SUBDATE(x, 7)", "SELECT DATE_SUB(x, INTERVAL 7 DAY)")

    def test_regex(self):
        self.validate_all(
            "SELECT REGEXP_LIKE(abc, '%foo%')",
            write={
                "doris": "SELECT REGEXP(abc, '%foo%')",
            },
        )

    def test_match(self):
        for operator, kind in (
            ("MATCH", None),
            ("MATCH_ANY", "ANY"),
            ("MATCH_ALL", "ALL"),
            ("MATCH_PHRASE", "PHRASE"),
            ("MATCH_PHRASE_PREFIX", "PHRASE_PREFIX"),
            ("MATCH_PHRASE_EDGE", "PHRASE_EDGE"),
            ("MATCH_REGEXP", "REGEXP"),
        ):
            with self.subTest(operator):
                match = self.validate_identity(
                    f"SELECT * FROM t WHERE c {operator} 'word1 word2'"
                ).find(exp.Match)
                self.assertEqual(match.args.get("kind"), kind)
                self.validate_identity(f"SELECT * FROM t WHERE c {operator} ('word1 word2')")

        self.validate_identity("SELECT * FROM t WHERE c MATCH_PHRASE 'word1 word2 ~3'")
        self.validate_identity("SELECT * FROM t WHERE c MATCH_PHRASE 'word1 word2 ~3+'")
        self.validate_identity(
            "SELECT * FROM t WHERE c MATCH 'hello world' USING ANALYZER std_analyzer"
        )
        self.validate_identity("SELECT * FROM t WHERE c MATCH_ANY 'x' USING ANALYZER `kw_analyzer`")
        self.validate_identity("SELECT * FROM t WHERE c MATCH 'x' USING ANALYZER 'standard'")
        self.validate_identity(
            "SELECT * FROM t WHERE c NOT MATCH_ANY ('x') USING ANALYZER 'standard'",
            "SELECT * FROM t WHERE NOT c MATCH_ANY ('x') USING ANALYZER 'standard'",
        )
        self.validate_identity(
            "SELECT * FROM t WHERE c match_regexp '^key_word.*'",
            "SELECT * FROM t WHERE c MATCH_REGEXP '^key_word.*'",
        )
        self.validate_identity(
            "SELECT * FROM t WHERE NOT c MATCH_ANY 'x' AND d MATCH_ALL 'y' OR e = 1"
        )
        self.validate_identity(
            "SELECT * FROM t WHERE c NOT MATCH_ALL 'word1 word2'",
            "SELECT * FROM t WHERE NOT c MATCH_ALL 'word1 word2'",
        )
        self.validate_identity("SELECT * FROM t WHERE c NOT LIKE 'x%'")
        self.validate_identity("SELECT c AS match_any FROM t", "SELECT c AS `match_any` FROM t")
        self.validate_identity("SELECT c match_any FROM t", "SELECT c AS `match_any` FROM t")
        self.validate_identity("SELECT c match_any", "SELECT c AS `match_any`")

        self.validate_all(
            "SELECT * FROM t WHERE c MATCH 'x'",
            write={
                "doris": "SELECT * FROM t WHERE c MATCH 'x'",
                "sqlite": "SELECT * FROM t WHERE c MATCH 'x'",
            },
        )
        self.validate_all(
            "SELECT * FROM t WHERE c MATCH_ALL 'x'",
            write={
                "doris": "SELECT * FROM t WHERE c MATCH_ALL 'x'",
                "sqlite": UnsupportedError,
            },
        )

        with self.assertRaises(ParseError):
            self.parse_one("SELECT * FROM t WHERE c MATCH_ANY 'x' USING ANALYZER")

    def test_analyze(self):
        self.validate_identity("ANALYZE TABLE tbl")
        self.validate_identity("ANALYZE DATABASE db")
        self.validate_identity("ANALYZE TABLE TBL(c1, c2)")

    def test_key(self):
        self.validate_identity("CREATE TABLE test_table (c1 INT, c2 INT) UNIQUE KEY (c1)")
        self.validate_identity("CREATE TABLE test_table (c1 INT, c2 INT) DUPLICATE KEY (c1)")
        self.validate_identity("CREATE MATERIALIZED VIEW test_table (c1 INT, c2 INT) KEY (c1)")

    def test_distributed(self):
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 INT) UNIQUE KEY (c1) DISTRIBUTED BY HASH (c1)"
        )
        self.validate_identity("CREATE TABLE test_table (c1 INT, c2 INT) DISTRIBUTED BY RANDOM")
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 INT) DISTRIBUTED BY RANDOM BUCKETS 1"
        )

    def test_partition(self):
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY RANGE (`c2`) (PARTITION `p201701` VALUES LESS THAN ('2017-02-01'), PARTITION `p201702` VALUES LESS THAN ('2017-03-01'))"
        )
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY RANGE (`c2`) (PARTITION `p201701` VALUES [('2017-01-01'), ('2017-02-01')), PARTITION `other` VALUES LESS THAN (MAXVALUE))"
        )
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY RANGE (`c2`) (FROM ('2000-11-14') TO ('2021-11-14') INTERVAL 2 YEAR)"
        )
        self.validate_identity("CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY (c2)")
        self.validate_identity("CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY (c1, c2)")
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT, c2 DATE) PARTITION BY (DATE_TRUNC(c2, 'MONTH'))"
        )
        self.validate_identity(
            "CREATE TABLE test_table (c1 INT) PARTITION BY LIST (`c1`) (PARTITION p1 VALUES IN (1, 2), PARTITION p2 VALUES IN (3))"
        )

    def test_table_alias_conversion(self):
        """Test conversion from postgres to Doris for DELETE/UPDATE statements with table aliases."""

        # Test cases for DELETE statements with table aliases
        self.validate_all(
            "DELETE FROM sales s WHERE s.id = 1",
            read={
                "postgres": "DELETE FROM sales AS s WHERE s.id = 1",
            },
            write={
                "doris": "DELETE FROM sales s WHERE s.id = 1",
                "postgres": "DELETE FROM sales AS s WHERE s.id = 1",
            },
        )

        # DELETE with multiple table references
        self.validate_all(
            "DELETE FROM orders o WHERE o.customer_id IN (SELECT c.id FROM customers AS c WHERE c.status_code = 'inactive')",
            read={
                "postgres": "DELETE FROM orders AS o WHERE o.customer_id IN (SELECT c.id FROM customers AS c WHERE c.status_code = 'inactive')",
            },
            write={
                "doris": "DELETE FROM orders o WHERE o.customer_id IN (SELECT c.id FROM customers AS c WHERE c.status_code = 'inactive')",
                "postgres": "DELETE FROM orders AS o WHERE o.customer_id IN (SELECT c.id FROM customers AS c WHERE c.status_code = 'inactive')",
            },
        )

        # DELETE with EXISTS clause
        self.validate_all(
            "DELETE FROM temp_data t WHERE NOT EXISTS(SELECT 1 FROM main_data AS m WHERE m.id = t.id)",
            read={
                "postgres": "DELETE FROM temp_data AS t WHERE NOT EXISTS(SELECT 1 FROM main_data AS m WHERE m.id = t.id)",
            },
            write={
                "doris": "DELETE FROM temp_data t WHERE NOT EXISTS(SELECT 1 FROM main_data AS m WHERE m.id = t.id)",
                "postgres": "DELETE FROM temp_data AS t WHERE NOT EXISTS(SELECT 1 FROM main_data AS m WHERE m.id = t.id)",
            },
        )

        # UPDATE statements with table aliases
        self.validate_all(
            "UPDATE employees e SET e.salary = e.salary * 1.1 WHERE e.department = 'IT'",
            read={
                "postgres": "UPDATE employees AS e SET e.salary = e.salary * 1.1 WHERE e.department = 'IT'",
            },
            write={
                "doris": "UPDATE employees e SET e.salary = e.salary * 1.1 WHERE e.department = 'IT'",
                "postgres": "UPDATE employees AS e SET e.salary = e.salary * 1.1 WHERE e.department = 'IT'",
            },
        )

        # UPDATE with multiple columns
        self.validate_all(
            "UPDATE accounts a SET a.balance = a.balance + 100, a.status_code = 'active' WHERE a.account_type = 'savings'",
            read={
                "postgres": "UPDATE accounts AS a SET a.balance = a.balance + 100, a.status_code = 'active' WHERE a.account_type = 'savings'",
            },
            write={
                "doris": "UPDATE accounts a SET a.balance = a.balance + 100, a.status_code = 'active' WHERE a.account_type = 'savings'",
                "postgres": "UPDATE accounts AS a SET a.balance = a.balance + 100, a.status_code = 'active' WHERE a.account_type = 'savings'",
            },
        )

        # UPDATE with multiple table references in subquery
        self.validate_all(
            "UPDATE prices p SET p.amount = p.amount * 0.9 WHERE p.product_id IN (SELECT pr.id FROM products AS pr JOIN categories AS c ON pr.category_id = c.id WHERE c.foo = 'Electronics')",
            read={
                "postgres": "UPDATE prices AS p SET p.amount = p.amount * 0.9 WHERE p.product_id IN (SELECT pr.id FROM products AS pr JOIN categories AS c ON pr.category_id = c.id WHERE c.foo = 'Electronics')",
            },
            write={
                "doris": "UPDATE prices p SET p.amount = p.amount * 0.9 WHERE p.product_id IN (SELECT pr.id FROM products AS pr JOIN categories AS c ON pr.category_id = c.id WHERE c.foo = 'Electronics')",
                "postgres": "UPDATE prices AS p SET p.amount = p.amount * 0.9 WHERE p.product_id IN (SELECT pr.id FROM products AS pr JOIN categories AS c ON pr.category_id = c.id WHERE c.foo = 'Electronics')",
            },
        )

    def test_rename_table(self):
        self.validate_all(
            "ALTER TABLE db.t1 RENAME TO db.t2",
            write={
                "snowflake": "ALTER TABLE db.t1 RENAME TO db.t2",
                "duckdb": "ALTER TABLE db.t1 RENAME TO t2",
                "doris": "ALTER TABLE db.t1 RENAME t2",
            },
        )

    def test_materialized_view_properties(self):
        # BUILD modes
        self.validate_identity("CREATE MATERIALIZED VIEW mv BUILD IMMEDIATE AS SELECT 1")
        self.validate_identity("CREATE MATERIALIZED VIEW mv BUILD DEFERRED AS SELECT 1")

        # REFRESH methods with triggers
        self.validate_identity("CREATE MATERIALIZED VIEW mv REFRESH COMPLETE ON MANUAL AS SELECT 1")
        self.validate_identity("CREATE MATERIALIZED VIEW mv REFRESH AUTO ON COMMIT AS SELECT 1")
        self.validate_identity(
            "CREATE MATERIALIZED VIEW mv REFRESH AUTO ON SCHEDULE EVERY 5 MINUTE STARTS '2025-01-01 00:00:00' AS SELECT 1"
        )

        # Combined BUILD and REFRESH
        self.validate_identity(
            "CREATE MATERIALIZED VIEW mv BUILD DEFERRED REFRESH AUTO ON SCHEDULE EVERY 10 MINUTE AS SELECT 1"
        )
