from decimal import Decimal, localcontext

from sqlglot import ErrorLevel, ParseError, UnsupportedError, exp, parse_one
from tests.dialects.test_dialect import Validator


class TestDoris(Validator):
    dialect = "doris"

    def test_regr_count(self):
        for source, expected in (
            (
                "SELECT REGR_COUNT(b, a) FROM aggtest",
                "SELECT COUNT(IF(NOT b IS NULL AND NOT a IS NULL, 1, NULL)) FROM aggtest",
            ),
            (
                "SELECT REGR_COUNT(CAST(y AS DOUBLE), CAST(x AS DOUBLE)) AS n FROM t",
                "SELECT COUNT(IF(NOT CAST(y AS DOUBLE) IS NULL AND NOT CAST(x AS DOUBLE) IS NULL, 1, NULL)) AS n FROM t",
            ),
            (
                "SELECT REGR_COUNT(NULL, -1.5) AS n",
                "SELECT COUNT(IF(NOT NULL IS NULL AND NOT -1.5 IS NULL, 1, NULL)) AS n",
            ),
            (
                "SELECT REGR_COUNT(NULL, NULL) AS n",
                "SELECT COUNT(IF(NOT NULL IS NULL AND NOT NULL IS NULL, 1, NULL)) AS n",
            ),
            (
                "SELECT REGR_COUNT(CAST(NULL AS DOUBLE), 1) AS n",
                "SELECT COUNT(IF(NOT CAST(NULL AS DOUBLE) IS NULL AND NOT 1 IS NULL, 1, NULL)) AS n",
            ),
            (
                'SELECT REGR_COUNT(t."Y", t."X") AS "Pair Count" FROM t',
                "SELECT COUNT(IF(NOT t.`Y` IS NULL AND NOT t.`X` IS NULL, 1, NULL)) AS `Pair Count` FROM t",
            ),
            (
                "SELECT g, REGR_COUNT(y, x) AS n FROM t GROUP BY g HAVING REGR_COUNT(y, x) > 0 ORDER BY g",
                "SELECT g, COUNT(IF(NOT y IS NULL AND NOT x IS NULL, 1, NULL)) AS n FROM t GROUP BY g HAVING COUNT(IF(NOT y IS NULL AND NOT x IS NULL, 1, NULL)) > 0 ORDER BY g NULLS LAST",
            ),
            (
                "SELECT (SELECT REGR_COUNT(y, x) FROM t) AS n",
                "SELECT (SELECT COUNT(IF(NOT y IS NULL AND NOT x IS NULL, 1, NULL)) FROM t) AS n",
            ),
            (
                "SELECT SUM(n) OVER () FROM (SELECT REGR_COUNT(y, x) AS n FROM t) AS c",
                "SELECT SUM(n) OVER () FROM (SELECT COUNT(IF(NOT y IS NULL AND NOT x IS NULL, 1, NULL)) AS n FROM t) AS c",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                original = tree.copy()
                self.assertEqual(tree.sql("doris", copy=False), expected)
                self.assertEqual(tree, original)
                self.validate_all(expected, read={"postgres": source})
                self.validate_identity(expected)

    def test_regr_count_exclusions(self):
        for source in (
            "SELECT REGR_COUNT(y, x) OVER () FROM t",
            "SELECT REGR_COUNT(y, x) FILTER(WHERE p) FROM t",
            "SELECT REGR_COUNT(DISTINCT y, x) FROM t",
            "SELECT REGR_COUNT(y, x ORDER BY z) FROM t",
            "SELECT REGR_COUNT(y) FROM t",
            "SELECT REGR_COUNT(RANDOM(), x) FROM t",
            "SELECT REGR_COUNT(y + 1, x) FROM t",
            "SELECT REGR_COUNT('1', x) FROM t",
            "SELECT REGR_COUNT(CAST('bad' AS DOUBLE), x) FROM t",
            "SELECT REGR_COUNT(CAST(y AS TEXT), x) FROM t",
            "SELECT REGR_COUNT($1, x) FROM t",
        ):
            with self.subTest(source):
                with self.assertRaises(UnsupportedError):
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

        self.validate_all(
            "SELECT REGR_COUNT(y, x) FROM t",
            write={"postgres": "SELECT REGR_COUNT(y, x) FROM t"},
        )

    def test_integer_generate_series(self):
        for source, expected in (
            (
                "SELECT * FROM GENERATE_SERIES(1, 3)",
                "SELECT * FROM (SELECT CAST(1 + number * 1 AS INT) AS generate_series FROM NUMBERS('number' = '3')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(CAST(1 AS INT), CAST(3 AS INT), CAST(1 AS INT))",
                "SELECT * FROM (SELECT CAST(1 + number * 1 AS INT) AS generate_series FROM NUMBERS('number' = '3')) AS generate_series",
            ),
            (
                "SELECT g, g.g FROM GENERATE_SERIES(3, -2, -2) AS g",
                "SELECT g, g.g FROM (SELECT CAST(3 + number * -2 AS INT) AS g FROM NUMBERS('number' = '3')) AS g",
            ),
            (
                'SELECT "Value" FROM GENERATE_SERIES(-3, 3, 2) AS "Series"("Value")',
                "SELECT `Value` FROM (SELECT CAST(-3 + number * 2 AS INT) AS `Value` FROM NUMBERS('number' = '4')) AS `Series`",
            ),
            (
                "SELECT COUNT(*) FROM GENERATE_SERIES(1, 0) g(x)",
                "SELECT COUNT(*) FROM (SELECT CAST(1 + number * 1 AS INT) AS x FROM NUMBERS('number' = '0')) AS g",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(0, 1, -1)",
                "SELECT * FROM (SELECT CAST(0 + number * -1 AS INT) AS generate_series FROM NUMBERS('number' = '0')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(-2147483648, -2147483646)",
                "SELECT * FROM (SELECT CAST(-2147483648 + number * 1 AS INT) AS generate_series FROM NUMBERS('number' = '3')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(2147483647, 2147483647, -2147483648)",
                "SELECT * FROM (SELECT CAST(2147483647 + number * -2147483648 AS INT) AS generate_series FROM NUMBERS('number' = '1')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(-2147483648, 2147483647)",
                "SELECT * FROM (SELECT CAST(-2147483648 + number * 1 AS INT) AS generate_series FROM NUMBERS('number' = '4294967296')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES(1, 1001)",
                "SELECT * FROM (SELECT CAST(1 + number * 1 AS INT) AS generate_series FROM NUMBERS('number' = '1001')) AS generate_series",
            ),
            (
                "SELECT * FROM GENERATE_SERIES((-1), (1)) g JOIN t ON g = t.x",
                "SELECT * FROM (SELECT CAST(-1 + number * 1 AS INT) AS g FROM NUMBERS('number' = '3')) AS g JOIN t ON g = t.x",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                original = tree.copy()
                self.assertEqual(tree.sql("doris", copy=False), expected)
                self.assertEqual(tree, original)
                self.validate_all(expected, read={"postgres": source})
                self.validate_identity(expected)

    def test_integer_generate_series_exclusions(self):
        for source in (
            "SELECT GENERATE_SERIES(1, 3)",
            "SELECT * FROM LATERAL GENERATE_SERIES(1, 3) g(x)",
            "SELECT * FROM t, GENERATE_SERIES(1, t.x) g(x)",
            "SELECT * FROM GENERATE_SERIES(1, 3, 0)",
            "SELECT * FROM GENERATE_SERIES(1.0, 3.0)",
            "SELECT * FROM GENERATE_SERIES(1::BIGINT, 3)",
            "SELECT * FROM GENERATE_SERIES(1, 2147483648)",
            "SELECT * FROM GENERATE_SERIES(1, 3) WITH ORDINALITY AS g(x, n)",
            "SELECT * FROM GENERATE_SERIES('2024-01-01'::DATE, '2024-01-02'::DATE, '1 day')",
        ):
            with self.subTest(source):
                with self.assertRaises(UnsupportedError):
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

        self.validate_all(
            "SELECT * FROM GENERATE_SERIES(1, 3)",
            write={"postgres": "SELECT * FROM GENERATE_SERIES(1, 3)"},
        )

    def test_integer_generate_series_nulls(self):
        for start, end, step in (
            ("NULL", "3", "1"),
            ("1", "NULL", "1"),
            ("1", "3", "NULL"),
            ("CAST(NULL AS INT)", "3", "0"),
            ("1", "CAST(NULL AS INT)", "0"),
            ("NULL", "NULL", "-1"),
            ("CAST(NULL AS INT)", "NULL", "NULL"),
        ):
            source = f"SELECT x FROM GENERATE_SERIES({start}, {end}, {step}) AS g(x)"
            comparison_step = f"({step})" if step.startswith("-") else step
            length = (
                f"CASE WHEN {start} IS NULL OR {end} IS NULL OR {comparison_step} IS NULL THEN 0 "
                f"WHEN ({comparison_step} > 0 AND {start} <= {end}) OR ({comparison_step} < 0 AND {start} >= {end}) "
                f"THEN (CAST({end} AS BIGINT) - CAST({start} AS BIGINT)) DIV NULLIF({step}, 0) + 1 "
                "ELSE 0 END"
            )
            expected = (
                f"SELECT x FROM (SELECT CAST({start} + _numbers.number * {step} AS INT) AS x "
                "FROM (SELECT 1 AS seed) AS _series LATERAL VIEW "
                f"EXPLODE_NUMBERS({length}) _numbers AS number) AS g"
            )
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                original = tree.copy()
                self.assertEqual(
                    tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                )
                self.assertEqual(tree, original)
                self.validate_all(expected, read={"postgres": source})
                self.validate_identity(expected)

        default = parse_one("SELECT * FROM GENERATE_SERIES(NULL, 3)", read="postgres").sql(
            "doris", unsupported_level=ErrorLevel.RAISE
        )
        self.assertIn("AS generate_series", default)
        self.assertIn("EXPLODE_NUMBERS(CASE WHEN NULL IS NULL OR 3 IS NULL OR 1 IS NULL", default)

    def test_integer_generate_series_nullable_scope(self):
        for source in (
            "SELECT * FROM GENERATE_SERIES(NULL::BIGINT, 3)",
            "SELECT * FROM GENERATE_SERIES(NULL::DECIMAL, 3)",
            "SELECT * FROM GENERATE_SERIES(NULL, 3.5)",
            "SELECT * FROM GENERATE_SERIES(NULL, 2147483648)",
            "SELECT * FROM GENERATE_SERIES(TRUE, 3)",
            "SELECT * FROM GENERATE_SERIES('1', 3)",
            "SELECT * FROM GENERATE_SERIES(:start, 3)",
            "SELECT * FROM GENERATE_SERIES(NULLIF(1, 1), 3)",
            "SELECT * FROM GENERATE_SERIES(CAST(1 AS INT), 3, 0)",
            "SELECT * FROM GENERATE_SERIES(1, 3, CAST(0 AS INT))",
            "SELECT * FROM t, GENERATE_SERIES(CAST(t.x AS INT), 3)",
        ):
            with self.subTest(source):
                with self.assertRaises(UnsupportedError):
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

    def test_sample_aggregate_nulls(self):
        for source_name, target_name in (
            ("STDDEV", "STDDEV_SAMP"),
            ("STDDEV_SAMP", "STDDEV_SAMP"),
            ("VARIANCE", "VAR_SAMP"),
            ("VAR_SAMP", "VAR_SAMP"),
        ):
            for argument in ("x", "DISTINCT x", "CAST(NULL AS DOUBLE)"):
                source = f"SELECT {source_name}({argument}) AS v FROM t"
                expected = (
                    f"SELECT IF(COUNT({argument}) < 2, NULL, {target_name}({argument})) AS v FROM t"
                )
                with self.subTest(source):
                    tree = parse_one(source, read="postgres")
                    original = tree.copy()
                    self.assertEqual(tree.sql("doris", copy=False), expected)
                    self.assertEqual(tree, original)
                    self.validate_all(expected, read={"postgres": source})
                    self.validate_identity(expected)

        for source, expected in (
            (
                "SELECT COVAR_SAMP(x, y) AS v FROM t",
                "SELECT IF(COUNT(IF(x IS NULL, NULL, y)) < 2, NULL, COVAR_SAMP(x, y)) AS v FROM t",
            ),
            (
                "SELECT g, VAR_SAMP(x) AS v FROM t GROUP BY g HAVING VAR_SAMP(x) IS NULL ORDER BY g",
                "SELECT g, IF(COUNT(x) < 2, NULL, VAR_SAMP(x)) AS v FROM t GROUP BY g HAVING IF(COUNT(x) < 2, NULL, VAR_SAMP(x)) IS NULL ORDER BY g NULLS LAST",
            ),
            (
                "SELECT (SELECT STDDEV_SAMP(x) FROM t WHERE FALSE) AS v",
                "SELECT (SELECT IF(COUNT(x) < 2, NULL, STDDEV_SAMP(x)) FROM t WHERE FALSE) AS v",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                original = tree.copy()
                self.assertEqual(tree.sql("doris", copy=False), expected)
                self.assertEqual(tree, original)
                self.validate_all(expected, read={"postgres": source})
                self.validate_identity(expected)

    def test_sample_aggregate_exclusions(self):
        for sql in (
            "SELECT STDDEV_SAMP(x) OVER () FROM t",
            "SELECT VAR_SAMP(x) OVER () FROM t",
            "SELECT COVAR_SAMP(x, y) OVER () FROM t",
            "SELECT STDDEV_SAMP(x) FILTER(WHERE p) FROM t",
            "SELECT VAR_SAMP(x ORDER BY y) FROM t",
            "SELECT STDDEV_POP(x), VAR_POP(x), COVAR_POP(x, y) FROM t",
        ):
            with self.subTest(sql):
                expected = sql.replace("ORDER BY y", "ORDER BY y NULLS LAST")
                self.assertEqual(parse_one(sql, read="postgres").sql("doris"), expected)

        for sql in (
            "SELECT STDDEV_SAMP(x), VAR_SAMP(x), COVAR_SAMP(x, y) FROM t",
            "SELECT STDDEV_POP(x), VAR_POP(x) FROM t",
        ):
            self.validate_identity(sql)

        self.validate_all(
            "SELECT VAR_SAMP(x) FROM t",
            read={"bigquery": "SELECT VAR_SAMP(x) FROM t"},
        )
        self.validate_all(
            "SELECT CASE COUNT(x) WHEN 0 THEN NULL WHEN 1 THEN 0 ELSE VAR_SAMP(x) END FROM t",
            read={"oracle": "SELECT VARIANCE(x) FROM t"},
        )
        self.validate_all(
            "SELECT STDDEV_POP(x), VAR_POP(x) FROM t",
            read={"mysql": "SELECT STDDEV(x), VARIANCE(x) FROM t"},
        )

    def test_boolean_every(self):
        self.validate_all(
            "SELECT MIN(b) AS v FROM t",
            read={
                "postgres": "SELECT EVERY(b) AS v FROM t",
                "bigquery": "SELECT LOGICAL_AND(b) AS v FROM t",
                "spark": "SELECT BOOL_AND(b) AS v FROM t",
                "snowflake": "SELECT BOOLAND_AGG(b) AS v FROM t",
            },
            write={"doris": "SELECT MIN(b) AS v FROM t"},
        )
        for source, expected in (
            ("SELECT EVERY(NOT b) AS v FROM t", "SELECT MIN(NOT b) AS v FROM t"),
            ("SELECT EVERY(DISTINCT b) FROM t", "SELECT MIN(DISTINCT b) FROM t"),
            (
                "SELECT EVERY(CAST(NULL AS BOOLEAN)) FROM t WHERE FALSE",
                "SELECT MIN(CAST(NULL AS BOOLEAN)) FROM t WHERE FALSE",
            ),
            (
                "SELECT g, EVERY(b) AS v FROM t GROUP BY g HAVING EVERY(b) ORDER BY g",
                "SELECT g, MIN(b) AS v FROM t GROUP BY g HAVING MIN(b) ORDER BY g NULLS LAST",
            ),
            (
                "SELECT (SELECT EVERY(b) FROM t) AS v",
                "SELECT (SELECT MIN(b) FROM t) AS v",
            ),
        ):
            with self.subTest(source):
                self.validate_all(expected, read={"postgres": source}, write={"doris": expected})
                self.validate_identity(expected)

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
            ("SELECT ARRAY[1, 2]", "SELECT CAST(ARRAY(1, 2) AS ARRAY<INT>)"),
            ("SELECT ARRAY[1, NULL, 2, 1]", "SELECT CAST(ARRAY(1, NULL, 2, 1) AS ARRAY<INT>)"),
            ("SELECT ARRAY[NULL]", "SELECT CAST(ARRAY(NULL) AS ARRAY<STRING>)"),
            ("SELECT ARRAY[]::INT[]", "SELECT CAST(ARRAY() AS ARRAY<INT>)"),
            (
                "SELECT ARRAY[[1, 2], [3, 4]]",
                "SELECT CAST(ARRAY(CAST(ARRAY(1, 2) AS ARRAY<INT>), CAST(ARRAY(3, 4) AS ARRAY<INT>)) AS ARRAY<ARRAY<INT>>)",
            ),
            (
                "SELECT ARRAY[1::BIGINT, NULL::BIGINT]",
                "SELECT CAST(ARRAY(CAST(1 AS BIGINT), CAST(NULL AS BIGINT)) AS ARRAY<BIGINT>)",
            ),
            (
                "SELECT ARRAY[1.25::DECIMAL(10, 2), NULL::DECIMAL(10, 2)]",
                "SELECT ARRAY(CAST(1.25 AS DECIMAL(10, 2)), CAST(NULL AS DECIMAL(10, 2)))",
            ),
            (
                "SELECT ARRAY['hello', 'world', '']",
                "SELECT CAST(ARRAY('hello', 'world', '') AS ARRAY<STRING>)",
            ),
            ("SELECT ARRAY[x, NULL] FROM t", "SELECT ARRAY(x, NULL) FROM t"),
            ("SELECT ARRAY[(SELECT 1)]", "SELECT CAST(ARRAY((SELECT 1)) AS ARRAY<INT>)"),
            (
                "SELECT ARRAY[(SELECT 1 WHERE FALSE)]",
                "SELECT CAST(ARRAY((SELECT 1 WHERE FALSE)) AS ARRAY<INT>)",
            ),
            (
                "SELECT ARRAY[(SELECT 1), (SELECT 2)]",
                "SELECT CAST(ARRAY((SELECT 1), (SELECT 2)) AS ARRAY<INT>)",
            ),
            (
                "SELECT any_value(v) FROM (VALUES (ARRAY['hello', 'world'])) AS v(v)",
                "SELECT ANY_VALUE(v) FROM (SELECT CAST(ARRAY('hello', 'world') AS ARRAY<STRING>) AS v) AS v",
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

    def test_postgres_array_element_types(self):
        for source, dtype in (
            ("ARRAY[0, 127, 128, NULL]", "INT"),
            ("ARRAY[-2147483648, 2147483647]", "INT"),
            ("ARRAY[2147483648, 1]", "BIGINT"),
            ("ARRAY[-9223372036854775808, 9223372036854775807]", "BIGINT"),
            ("ARRAY[(SELECT 2147483648 AS n)]", "BIGINT"),
            ("ARRAY[(SELECT NULL)]", "STRING"),
            ("ARRAY[NULL, NULL]", "STRING"),
            ("ARRAY['世界', '', NULL]", "STRING"),
            ("ARRAY[TRUE, FALSE, NULL]", "BOOLEAN"),
            ("ARRAY[CAST(1 AS SMALLINT), CAST(2 AS INT)]", "INT"),
            ("ARRAY[CAST(1 AS REAL), 2]", "FLOAT"),
        ):
            with self.subTest(source):
                tree = parse_one(f"SELECT {source} AS xs", read="postgres")
                original = tree.copy()
                target = tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE)
                self.assertEqual(tree, original)
                self.assertIsInstance(parse_one(target, read="doris").expressions[0].this, exp.Cast)
                self.assertTrue(target.endswith(f"AS ARRAY<{dtype}>) AS xs"))
                self.validate_identity(target)

        # Do not guess a schema or use generic DOUBLE inference for PG NUMERIC.
        for source in (
            "ARRAY[x, NULL]",
            "ARRAY[:x, NULL]",
            "ARRAY[1.25, 2]",
            "ARRAY[9223372036854775808]",
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                self.assertNotIn("element_type", tree.args)
                self.assertFalse(tree.sql("doris").startswith("CAST("))

    def test_postgres_array_unknown_inputs(self):
        for source, expected in (
            ("ARRAY['1', 2, NULL]", "CAST(ARRAY(CAST('1' AS INT), 2, NULL) AS ARRAY<INT>)"),
            ("ARRAY[1, '  +01 ']", "CAST(ARRAY(1, CAST('1' AS INT)) AS ARRAY<INT>)"),
            ("ARRAY['1_000', 1]", "CAST(ARRAY(CAST('1000' AS INT), 1) AS ARRAY<INT>)"),
            ("ARRAY['-0x80', 1]", "CAST(ARRAY(CAST('-128' AS INT), 1) AS ARRAY<INT>)"),
            ("ARRAY['0x_7f', 1]", "CAST(ARRAY(CAST('127' AS INT), 1) AS ARRAY<INT>)"),
            ("ARRAY['0o17', 1]", "CAST(ARRAY(CAST('15' AS INT), 1) AS ARRAY<INT>)"),
            ("ARRAY['0b101', 1]", "CAST(ARRAY(CAST('5' AS INT), 1) AS ARRAY<INT>)"),
            (
                "ARRAY['2147483648', 1::BIGINT]",
                "CAST(ARRAY(CAST('2147483648' AS BIGINT), CAST(1 AS BIGINT)) AS ARRAY<BIGINT>)",
            ),
            (
                "ARRAY[TRUE, 'yes', 'off']",
                "CAST(ARRAY(TRUE, CAST('true' AS BOOLEAN), CAST('false' AS BOOLEAN)) AS ARRAY<BOOLEAN>)",
            ),
            (
                "ARRAY[FALSE, (' ON '), 'f']",
                "CAST(ARRAY(FALSE, CAST('true' AS BOOLEAN), CAST('false' AS BOOLEAN)) AS ARRAY<BOOLEAN>)",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                self.assertEqual(
                    tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                )
                self.assertEqual(tree, before)
                self.validate_identity(expected)
                self.assertEqual(parse_one(tree.sql("postgres"), read="postgres"), tree)

        self.assertEqual(
            parse_one("ARRAY['" + "0" * 5000 + "1', 1]", read="postgres").sql("doris"),
            "CAST(ARRAY(CAST('1' AS INT), 1) AS ARRAY<INT>)",
        )

        # An explicit constructor cast supplies the element input type, including
        # to nested constructors, but not across subqueries or an inner cast.
        for source, expected in (
            (
                "ARRAY['2147483648', 1]::BIGINT[]",
                "CAST(CAST(ARRAY(CAST('2147483648' AS BIGINT), 1) AS ARRAY<BIGINT>) AS ARRAY<BIGINT>)",
            ),
            (
                "ARRAY['01', 1]::TEXT[]",
                "CAST(CAST(ARRAY('01', 1) AS ARRAY<STRING>) AS ARRAY<STRING>)",
            ),
            (
                "(ARRAY['0x7fff', 1])::SMALLINT[]",
                "CAST((CAST(ARRAY(CAST('32767' AS SMALLINT), 1) AS ARRAY<SMALLINT>)) AS ARRAY<SMALLINT>)",
            ),
            (
                "ARRAY[ARRAY['2147483648', 1], ARRAY[2, 3]]::BIGINT[][]",
                "CAST(CAST(ARRAY(CAST(ARRAY(CAST('2147483648' AS BIGINT), 1) AS ARRAY<BIGINT>), CAST(ARRAY(2, 3) AS ARRAY<BIGINT>)) AS ARRAY<ARRAY<BIGINT>>) AS ARRAY<ARRAY<BIGINT>>)",
            ),
            ("ARRAY['1.5', 1]::DECIMAL(10, 2)[]", "CAST(ARRAY('1.5', 1) AS ARRAY<DECIMAL(10, 2)>)"),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                self.assertEqual(
                    tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                )
                self.assertEqual(tree, before)
                self.validate_identity(expected)

        # Invalid source inputs do not silently become Doris NULLs or rounded integers.
        for source in (
            "ARRAY['1__0', 1]",
            "ARRAY['12.0', 1]",
            "ARRAY['1e2', 1]",
            "ARRAY['2147483648', 1]",
            "ARRAY['32768', 1::SMALLINT]",
            "ARRAY['9223372036854775808', 1::BIGINT]",
            "ARRAY['１', 1]",
            "ARRAY[TRUE, 'o']",
            "ARRAY[FALSE, 'truth']",
            "(SELECT ARRAY['2147483648', 1])::BIGINT[]",
            "(ARRAY['2147483648', 1]::INT[])::BIGINT[]",
        ):
            with self.subTest(source):
                with self.assertRaisesRegex(UnsupportedError, "array input"):
                    parse_one(source, read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

        for dialect in ("doris", "hive"):
            self.assertEqual(
                parse_one("ARRAY('0x7f', 1)", read=dialect).sql("doris"),
                "ARRAY('0x7f', 1)",
            )

    def test_postgres_array_float_inputs(self):
        for source, expected in (
            (
                "ARRAY[1::REAL, '1e2', NULL, '1e2']",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST(1 AS FLOAT), CAST('100' AS DOUBLE), NULL, CAST('100' AS DOUBLE)))",
            ),
            (
                "ARRAY['  +1.5 ', 1::DOUBLE PRECISION]",
                "CAST(ARRAY(CAST('1.5' AS DOUBLE), CAST(1 AS DOUBLE)) AS ARRAY<DOUBLE>)",
            ),
            (
                "ARRAY['-0', 1::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('-0' AS DOUBLE), CAST(1 AS FLOAT)))",
            ),
            (
                "ARRAY[1::REAL, '7.038531e-26']",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST(1 AS FLOAT), CAST('0.00000000000000000000000007038530691851209120859188017140306974105991300039164570989669300615787506103515625' AS DOUBLE)))",
            ),
            (
                "ARRAY['1.0000000596046447753906250000000000001', 1::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('1.00000011920928955078125' AS DOUBLE), CAST(1 AS FLOAT)))",
            ),
            (
                "ARRAY['1.000000059604644775390625', 1::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('1' AS DOUBLE), CAST(1 AS FLOAT)))",
            ),
            (
                "ARRAY['1.5', 1]::REAL[]",
                "CAST(ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('1.5' AS DOUBLE), 1)) AS ARRAY<FLOAT>)",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                self.assertEqual(
                    tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                )
                self.assertEqual(tree, before)
                self.assertEqual(parse_one(tree.sql("postgres"), read="postgres"), tree)
                self.validate_identity(expected)

        for value in (
            "1e400",
            "1e-4000",
            "1e99999999999999999999",
            "NaN",
            "Infinity",
            "1_000",
            "",
            "１",
            "1" * 4097,
        ):
            with self.subTest(value):
                with self.assertRaisesRegex(UnsupportedError, "array input"):
                    parse_one(f"ARRAY[1::REAL, '{value}']", read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

        for dialect in ("hive", "doris"):
            self.assertEqual(
                parse_one("ARRAY(CAST(1 AS FLOAT), '1e2')", read=dialect).sql("doris"),
                "ARRAY(CAST(1 AS FLOAT), '1e2')",
            )

        for dtype, value, hexadecimal in (
            ("REAL", "7.038531e-26", "0x1.5c87fap-84"),
            ("REAL", "1.000000059604644775390625", "0x1p0"),
            ("REAL", "1.0000000596046447753906250000000000001", "0x1.000002p0"),
            ("REAL", "1.000000178813934326171875", "0x1.000004p0"),
            ("REAL", "1e-45", "0x1p-149"),
            ("REAL", "3.4028234663852886e38", "0x1.fffffep127"),
            ("DOUBLE PRECISION", "9007199254740993", "0x1p53"),
            ("DOUBLE PRECISION", "4.9406564584124654e-324", "0x1p-1074"),
            ("DOUBLE PRECISION", "1.7976931348623157e308", "0x1.fffffffffffffp1023"),
        ):
            with self.subTest(dtype=dtype, value=value), localcontext() as context:
                context.prec = 2
                target = parse_one(f"ARRAY['{value}', CAST(1 AS {dtype})]", read="postgres").sql(
                    "doris", unsupported_level=ErrorLevel.RAISE
                )
                literal = next(parse_one(target, read="doris").find_all(exp.Literal))
                self.assertEqual(
                    Decimal(literal.to_py()), Decimal.from_float(float.fromhex(hexadecimal))
                )

        for source in (
            "ARRAY[ARRAY['7.038531e-26', 1::REAL], ARRAY['1e2', NULL::REAL]]",
            "ARRAY[ARRAY['7.038531e-26', 1], ARRAY['1e2', NULL]]::REAL[][]",
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                target = tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE)
                self.assertEqual(tree, before)
                self.assertEqual(target.count("ARRAY_MAP"), 2)
                self.assertIn("ARRAY<ARRAY<FLOAT>>", target)
                self.validate_identity(target)

    def test_postgres_array_real_cast_inputs(self):
        for source, expected in (
            (
                "ARRAY[0.1::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('0.100000001490116119384765625' AS DOUBLE)))",
            ),
            (
                "ARRAY['1e2', 0.1::REAL, 2::DOUBLE PRECISION]",
                "CAST(ARRAY(CAST('100' AS DOUBLE), CAST('0.100000001490116119384765625' AS DOUBLE), CAST(2 AS DOUBLE)) AS ARRAY<DOUBLE>)",
            ),
            (
                "ARRAY['1e2', '7.038531e-26'::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('100' AS DOUBLE), CAST('0.00000000000000000000000007038530691851209120859188017140306974105991300039164570989669300615787506103515625' AS DOUBLE)))",
            ),
            (
                "ARRAY['1.0000000596046447753906250000000000001'::DOUBLE PRECISION::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('1' AS DOUBLE)))",
            ),
            (
                "ARRAY['1.0000000596046447753906250000000000001'::REAL::DOUBLE PRECISION]",
                "CAST(ARRAY(CAST('1.00000011920928955078125' AS DOUBLE)) AS ARRAY<DOUBLE>)",
            ),
            (
                "ARRAY[-0.1::REAL, NULL::REAL]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(-CAST('0.100000001490116119384765625' AS DOUBLE), CAST(NULL AS FLOAT)))",
            ),
            (
                "ARRAY[CAST(-0.1 AS REAL)]",
                "ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY(CAST('-0.100000001490116119384765625' AS DOUBLE)))",
            ),
            (
                "ARRAY[CAST(-CAST(0.1 AS REAL) AS DOUBLE PRECISION)]",
                "CAST(ARRAY(CAST('-0.100000001490116119384765625' AS DOUBLE)) AS ARRAY<DOUBLE>)",
            ),
            (
                "ARRAY[1::REAL, 0.5::REAL, NULL::REAL]",
                "CAST(ARRAY(CAST(1 AS FLOAT), CAST(0.5 AS FLOAT), CAST(NULL AS FLOAT)) AS ARRAY<FLOAT>)",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                self.assertEqual(
                    tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE), expected
                )
                self.assertEqual(tree, before)
                self.assertEqual(exp.Expression.load(tree.dump()), tree)
                self.assertEqual(parse_one(tree.sql("postgres"), read="postgres"), tree)
                self.validate_identity(expected)

        for source, hexadecimal in (
            ("ARRAY[(-1.0000000596046447753906250000000000001)::REAL]", "-0x1.000002p0"),
            ("ARRAY['-1.0000000596046447753906250000000000001'::REAL]", "-0x1.000002p0"),
            ("ARRAY['1e-45'::REAL]", "0x1p-149"),
            ("ARRAY['3.4028234663852886e38'::REAL]", "0x1.fffffep127"),
        ):
            with self.subTest(source), localcontext() as context:
                context.prec = 2
                target = parse_one(source, read="postgres").sql(
                    "doris", unsupported_level=ErrorLevel.RAISE
                )
                literal = next(
                    v for v in parse_one(target, read="doris").find_all(exp.Literal) if v.is_string
                )
                self.assertEqual(
                    Decimal(literal.to_py()), Decimal.from_float(float.fromhex(hexadecimal))
                )

        # No PostgreSQL ARRAY type hint, no change to other dialects or scalar CASTs.
        for dialect in ("hive", "doris"):
            self.assertEqual(
                parse_one("ARRAY(CAST(0.1 AS FLOAT))", read=dialect).sql("doris"),
                "ARRAY(CAST(0.1 AS FLOAT))",
            )
        self.assertEqual(parse_one("0.1::REAL", read="postgres").sql("doris"), "CAST(0.1 AS FLOAT)")
        for source in ("ARRAY[x::REAL]", "ARRAY['NaN'::REAL]"):
            self.assertNotIn("ARRAY_MAP", parse_one(source, read="postgres").sql("doris"))

    def test_postgres_array_scalar_real_inputs(self):
        exact = "CAST('0.100000001490116119384765625' AS DOUBLE)"
        for source, expected in (
            (
                "ARRAY[(SELECT 0.1::REAL), 2::DOUBLE PRECISION]",
                f"CAST(ARRAY((SELECT {exact}), CAST(2 AS DOUBLE)) AS ARRAY<DOUBLE>)",
            ),
            (
                "ARRAY[(SELECT 0.1::REAL AS value)]",
                f"ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY((SELECT {exact} AS `value`)))",
            ),
            (
                "ARRAY[(SELECT -0.1::REAL)]",
                f"ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY((SELECT -{exact})))",
            ),
            (
                "ARRAY[(SELECT (SELECT 0.1::REAL))]",
                f"ARRAY_MAP(_pg_array_value -> CAST(_pg_array_value AS FLOAT), ARRAY((SELECT (SELECT {exact}))))",
            ),
            (
                "ARRAY[(SELECT 0.1::REAL)]::DOUBLE PRECISION[]",
                f"CAST(CAST(ARRAY((SELECT {exact})) AS ARRAY<DOUBLE>) AS ARRAY<DOUBLE>)",
            ),
        ):
            with self.subTest(source):
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                self.assertEqual(tree.sql("doris", copy=False), expected)
                self.assertEqual(tree, before)
                self.assertEqual(exp.Expression.load(tree.dump()), tree)
                self.validate_identity(expected)

        # Do not move coercion through a table, row filter, set operation or aggregate.
        for source in (
            "ARRAY[(SELECT 0.1::REAL WHERE FALSE)]",
            "ARRAY[(SELECT 0.1::REAL FROM t)]",
            "ARRAY[(SELECT 0.1::REAL LIMIT 0)]",
            "ARRAY[(SELECT 0.1::REAL UNION ALL SELECT 0.2::REAL)]",
            "ARRAY[(SELECT SUM(x)::REAL FROM t)]",
            "ARRAY[(SELECT NULL::REAL)]",
            "ARRAY[(SELECT 0.5::REAL)]",
        ):
            with self.subTest(source):
                self.assertNotIn(
                    "0.100000001490116119384765625", parse_one(source, read="postgres").sql("doris")
                )

        self.assertEqual(
            parse_one("(SELECT 0.1::REAL)", read="postgres").sql("doris"),
            "(SELECT CAST(0.1 AS FLOAT))",
        )

    def test_postgres_array_hex_float_inputs(self):
        for source, exact, dtype in (
            ("ARRAY[1::REAL, '0x1.000002p0']", "1.00000011920928955078125", "FLOAT"),
            ("ARRAY[1::DOUBLE PRECISION, '0X.8']", "0.5", "DOUBLE"),
            ("ARRAY['-0x.8'::REAL]", "-0.5", "FLOAT"),
            ("ARRAY['0x1.000001p0'::REAL]", "1", "FLOAT"),
            ("ARRAY['0x1.0000010000000000000001p0'::REAL]", "1.00000011920928955078125", "FLOAT"),
            (
                "ARRAY['0x1.0000000000001p0'::DOUBLE PRECISION]",
                "1.0000000000000002220446049250313080847263336181640625",
                "DOUBLE",
            ),
            ("ARRAY[(SELECT '0x1.8p+01'::REAL)]", "3", "FLOAT"),
        ):
            with self.subTest(source), localcontext() as context:
                context.prec = 2
                tree = parse_one(source, read="postgres")
                before = tree.copy()
                target = tree.sql("doris", copy=False, unsupported_level=ErrorLevel.RAISE)
                self.assertIn(f"CAST('{exact}' AS DOUBLE)", target)
                self.assertIn("AS FLOAT)" if dtype == "FLOAT" else "AS ARRAY<DOUBLE>", target)
                self.assertEqual(tree, before)
                self.assertEqual(exp.Expression.load(tree.dump()), tree)
                self.validate_identity(target)

        for value in ("0x1p128", "0x1p-150", "0x.p0", "0x1p20001", "0x1p" + "9" * 4096):
            with self.subTest(value):
                with self.assertRaises(UnsupportedError):
                    parse_one(f"ARRAY[1::REAL, '{value}']", read="postgres").sql(
                        "doris", unsupported_level=ErrorLevel.RAISE
                    )

        self.assertEqual(
            parse_one("'0x1.8p0'::DOUBLE PRECISION", read="postgres").sql("doris"),
            "CAST('0x1.8p0' AS DOUBLE)",
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
