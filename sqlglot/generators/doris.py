from __future__ import annotations

import re
import typing as t
from decimal import Decimal, InvalidOperation

from sqlglot import exp
from sqlglot.dialects.dialect import (
    approx_count_distinct_sql,
    no_filter_sql,
    property_sql,
    rename_func,
    time_format,
    weekstart_unit_to_str,
)
from sqlglot.generators.mysql import MySQLGenerator
from sqlglot.optimizer.annotate_types import annotate_types


_PG_INTEGER_INPUT = re.compile(
    r"[+-]?(?:[0-9](?:_?[0-9])*|0[xX]_?[0-9a-fA-F](?:_?[0-9a-fA-F])*"
    r"|0[oO]_?[0-7](?:_?[0-7])*|0[bB]_?[01](?:_?[01])*)"
)
_PG_DECIMAL_FLOAT_INPUT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_PG_HEX_FLOAT_INPUT = re.compile(
    r"([+-]?)0[xX]([0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?|\.[0-9a-fA-F]+)"
    r"(?:[pP]([+-]?[0-9]+))?"
)


def _lag_lead_sql(self, expression: exp.Lag | exp.Lead) -> str:
    return self.func(
        "LAG" if isinstance(expression, exp.Lag) else "LEAD",
        expression.this,
        expression.args.get("offset") or exp.Literal.number(1),
        expression.args.get("default") or exp.null(),
    )


def _sample_aggregate_sql(
    self, expression: exp.Stddev | exp.StddevSamp | exp.Variance | exp.CovarSamp
) -> str:
    if isinstance(expression, exp.Variance) and not expression.args.get(
        "null_on_insufficient_samples"
    ):
        return self.variance_sql(expression)

    name = (
        "COVAR_SAMP"
        if isinstance(expression, exp.CovarSamp)
        else "VAR_SAMP"
        if isinstance(expression, exp.Variance)
        else "STDDEV_SAMP"
    )
    aggregate = self.func(name, expression.this, expression.expression)
    context = expression.find_ancestor(exp.Window, exp.Filter, exp.WithinGroup, exp.Select)
    if (
        not expression.args.get("null_on_insufficient_samples")
        or isinstance(context, (exp.Window, exp.Filter, exp.WithinGroup))
        or isinstance(expression.this, exp.Order)
    ):
        return aggregate

    value = expression.this.copy()
    if isinstance(expression, exp.CovarSamp):
        # COUNT ignores a pair when either input is NULL.
        value = exp.If(
            this=value.is_(exp.null()), true=exp.null(), false=expression.expression.copy()
        )
    count = exp.Count(this=value)
    return self.func(
        "IF", exp.LT(this=count, expression=exp.Literal.number(2)), exp.null(), aggregate
    )


class DorisGenerator(MySQLGenerator):
    INTEGER_SERIES_LENGTH = exp.maybe_parse(
        "CASE WHEN :start IS NULL OR :end IS NULL OR :step IS NULL THEN 0 "
        "WHEN (:step > 0 AND :start <= :end) OR (:step < 0 AND :start >= :end) "
        "THEN (CAST(:end AS BIGINT) - CAST(:start AS BIGINT)) DIV NULLIF(:step, 0) + 1 "
        "ELSE 0 END",
        dialect="mysql",
    )

    def regrcount_sql(self, expression: exp.RegrCount) -> str:
        args = (expression.this, expression.expression)
        context = expression.find_ancestor(exp.Window, exp.Filter, exp.WithinGroup, exp.Select)
        supported = not isinstance(context, (exp.Window, exp.Filter, exp.WithinGroup))
        for arg in args:
            if arg is None:
                supported = False
                break
            value = arg.unnest()
            while type(value) is exp.Cast and value.to.is_type(*exp.DataType.NUMERIC_TYPES):
                value = value.this.unnest()
            if (
                not isinstance(value, exp.Column)
                and not value.is_number
                and not (
                    value.is_leaf()
                    and not isinstance(value, exp.Func)
                    and annotate_types(
                        exp.Coalesce(this=value.copy(), expressions=[exp.Literal.number(1)]),
                        dialect="postgres",
                    ).is_type(exp.DType.INT)
                )
            ):
                supported = False
                break

        if not supported:
            self.unsupported(
                "Doris REGR_COUNT requires two numeric columns or constants, optionally with "
                "numeric CASTs, without DISTINCT, ORDER BY, FILTER or OVER"
            )
            return self.func("REGR_COUNT", *args)

        return self.func(
            "COUNT",
            exp.If(
                this=exp.and_(*(arg.copy().is_(exp.null()).not_() for arg in args)),
                true=exp.Literal.number(1),
                false=exp.null(),
            ),
        )

    def table_sql(self, expression: exp.Table, sep: str = " AS ") -> str:
        ancestor = expression.find_ancestor(exp.Update, exp.Delete, exp.Select)
        if not isinstance(ancestor, exp.Select):
            sep = " "
        series = expression.this
        if not isinstance(series, exp.ExplodingGenerateSeries):
            return super().table_sql(expression, sep=sep)

        args = [series.args.get(key) for key in ("start", "end", "step")]
        args[2] = args[2] if args[2] is not None else exp.Literal.number(1)
        args = [arg.unnest() if arg is not None else None for arg in args]
        alias = expression.args.get("alias")
        if (
            any(arg is None for arg in args)
            or any(expression.args.get(key) for key in ("catalog", "db", "ordinality"))
            or series.args.get("is_end_exclusive")
            or (alias and len(alias.columns) > 1)
        ):
            return super().table_sql(expression, sep=sep)

        constants = []
        for arg in args:
            constant = arg
            while isinstance(constant, exp.Cast) and constant.to.is_type(exp.DType.INT):
                constant = constant.this.unnest()
            if constant.is_int:
                value = constant.to_py()
                if value < -(2**31) or value >= 2**31:
                    return super().table_sql(expression, sep=sep)
            elif (
                not constant.is_leaf()
                or isinstance(constant, exp.Func)
                or not annotate_types(
                    exp.Coalesce(this=constant.copy(), expressions=[exp.Literal.number(1)]),
                    dialect="postgres",
                ).is_type(exp.DType.INT)
            ):
                return super().table_sql(expression, sep=sep)
            constants.append(constant)

        name = alias.this.copy() if alias and alias.this else exp.to_identifier("generate_series")
        column = alias.columns[0].copy() if alias and alias.columns else name.copy()
        query = exp.select()
        if all(arg.is_int for arg in constants):
            start, end, step = (arg.to_py() for arg in constants)
            if not step:
                return super().table_sql(expression, sep=sep)
            # NUMBERS requires a string literal, not a runtime length expression.
            count = max(0, (end - start) // step + 1)
            query = query.from_(
                exp.Table(
                    this=exp.func(
                        "NUMBERS",
                        exp.EQ(
                            this=exp.Literal.string("number"),
                            expression=exp.Literal.string(str(count)),
                        ),
                    )
                )
            )
            start, step = exp.convert(start), exp.convert(step)
            number = exp.column("number")
        else:
            # Eligibility is type-based; NULL semantics remain in the runtime CASE.
            start, end, step = (arg.copy() for arg in args)
            length = exp.replace_placeholders(
                self.INTEGER_SERIES_LENGTH.copy(), start=start, end=end, step=step
            )
            query = query.from_(
                exp.select(exp.alias_(exp.Literal.number(1), "seed")).subquery("_series")
            )
            query.set(
                "laterals",
                [
                    exp.Lateral(
                        this=exp.func("EXPLODE_NUMBERS", length),
                        view=True,
                        alias=exp.TableAlias(
                            this=exp.to_identifier("_numbers"),
                            columns=[exp.to_identifier("number")],
                        ),
                    )
                ],
            )
            number = exp.column("number", table="_numbers")
        value = exp.Add(this=start, expression=exp.Mul(this=number, expression=step))
        query = query.select(exp.alias_(exp.cast(value, exp.DType.INT), column))
        table = expression.copy()
        table.set("this", exp.Subquery(this=query))
        table.set("alias", exp.TableAlias(this=name))
        return super().table_sql(table, sep=sep)

    def explodinggenerateseries_sql(self, expression: exp.ExplodingGenerateSeries) -> str:
        self.unsupported(
            "Doris GENERATE_SERIES requires constant INT-compatible table arguments without "
            "LATERAL or WITH ORDINALITY; zero step on non-NULL inputs is unsupported"
        )
        return super().explodinggenerateseries_sql(expression)

    def filter_sql(self, expression: exp.Filter) -> str:
        aggregate = expression.this
        if (
            type(aggregate) not in (exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count, exp.AnyValue)
            or isinstance(expression.find_ancestor(exp.Window, exp.Select), exp.Window)
            or isinstance(aggregate.this, exp.Order)
            or aggregate.expressions
            or any(column.is_star for column in aggregate.find_all(exp.Column))
            or (isinstance(aggregate.this, exp.Distinct) and len(aggregate.this.expressions) != 1)
        ):
            return super().filter_sql(expression)

        if isinstance(aggregate, exp.Count) and (aggregate.this is None or aggregate.this.is_star):
            aggregate = aggregate.copy()
            aggregate.set(
                "this",
                exp.If(
                    this=expression.expression.this.copy(),
                    true=exp.Literal.number(1),
                    false=exp.null(),
                ),
            )
            return self.sql(aggregate)

        return no_filter_sql(self, expression.copy())

    def arrayagg_sql(self, expression: exp.ArrayAgg) -> str:
        context = expression.find_ancestor(exp.Window, exp.Filter, exp.WithinGroup, exp.Select)
        if isinstance(context, (exp.Window, exp.Filter, exp.WithinGroup)):
            return self.func("COLLECT_LIST", expression.this)

        this = expression.this
        order = this if isinstance(this, exp.Order) else None
        if order:
            this = order.this
            if isinstance(this, exp.Distinct) and len(this.expressions) != 1:
                return self.func("COLLECT_LIST", expression.this)
            value = this.expressions[0] if isinstance(this, exp.Distinct) else this
            if (
                len(order.expressions) != 1
                or order.expressions[0].this != value
                or value.find(exp.Query, exp.Collate)
                or any(not isinstance(node, exp.Cast) for node in value.find_all(exp.Func))
            ):
                return self.func("COLLECT_LIST", expression.this)

        if expression.args.get("nulls_excluded"):
            return self.func("COLLECT_LIST", expression.this)

        aggregate = self.func("ARRAY_AGG", this)
        if order:
            # Slice the NULL prefix; Doris rejects lambda arguments in HAVING.
            ordered = order.expressions[0]
            all_values = exp.ArrayAgg(this=this.copy())
            nonnull_values = exp.ArrayAgg(this=this.copy(), nulls_excluded=True)
            null_count = exp.ArraySize(this=all_values) - exp.ArraySize(this=nonnull_values.copy())
            nulls = self.func(
                "ARRAY_SLICE", self.func("ARRAY_SORT", aggregate), exp.Literal.number(1), null_count
            )
            nonnull = self.func(
                "ARRAY_REVERSE_SORT" if ordered.args.get("desc") else "ARRAY_SORT", nonnull_values
            )
            parts = (nulls, nonnull) if ordered.args.get("nulls_first") else (nonnull, nulls)
            aggregate = self.func("ARRAY_CONCAT", *parts)

        if expression.args.get("null_on_empty"):
            return self.func("NULLIF", aggregate, self.func("ARRAY"))

        return aggregate

    def cast_sql(self, expression: exp.Cast, safe_prefix: str | None = None) -> str:
        value = expression.this.unnest()
        if (
            expression.to.is_type(exp.DType.ARRAY)
            and isinstance(value, exp.Array)
            and any(array.args.get("element_type") for array in value.find_all(exp.Array))
        ):
            # PG applies the explicit constructor cast to unknown elements,
            # overriding the constructor's provisional common type.
            expression = expression.copy()
            self._array_cast_types(t.cast(exp.Array, expression.this.unnest()), expression.to)
        return super().cast_sql(expression, safe_prefix=safe_prefix)

    def _array_cast_types(self, array: exp.Array, dtype: exp.DataType) -> None:
        element = t.cast(exp.DataType, dtype.expressions[0])
        array.set(
            "element_type",
            element.copy()
            if element.is_type(
                exp.DType.ARRAY,
                exp.DType.TEXT,
                exp.DType.BOOLEAN,
                exp.DType.SMALLINT,
                exp.DType.INT,
                exp.DType.BIGINT,
                exp.DType.FLOAT,
                exp.DType.DOUBLE,
            )
            else None,
        )
        if element.is_type(exp.DType.ARRAY):
            for value in array.expressions:
                nested = value.unnest()
                if isinstance(nested, exp.Array):
                    self._array_cast_types(nested, element)

    def array_sql(self, expression: exp.Array) -> str:
        expressions = expression.expressions
        if (
            len(expressions) == 1
            and isinstance(expressions[0], exp.Query)
            and not expression.args.get("value_constructor")
        ):
            self.unsupported("Doris does not support ARRAY subquery constructors")
        element_type = expression.args.get("element_type")
        if element_type:
            leaf_type = element_type
            while leaf_type.is_type(exp.DType.ARRAY):
                leaf_type = leaf_type.expressions[0]
            if leaf_type.is_type(exp.DType.DECIMAL):
                return self._bounded_numeric_array_sql(expression)
            array = expression.copy()
            array.set("element_type", None)
            inputs = [self._coerce_array_input(value, element_type) for value in expressions]
            runtime_float = element_type.is_type(exp.DType.FLOAT) and any(
                value != converted for value, converted in zip(expressions, inputs)
            )
            array.set("expressions", inputs)
            if runtime_float:
                # Keep FLOAT conversion inside a lambda: Doris's constant FLOAT
                # array cast can change the already rounded input once more.
                parameter = exp.to_identifier("_pg_array_value")
                return self.func(
                    "ARRAY_MAP",
                    exp.Lambda(this=exp.cast(parameter, exp.DType.FLOAT), expressions=[parameter]),
                    array,
                )
            return super().cast_sql(
                exp.cast(
                    array,
                    exp.DataType(
                        this=exp.DType.ARRAY, expressions=[element_type.copy()], nested=True
                    ),
                )
            )
        return self.func("ARRAY", *expressions)

    def _bounded_numeric_array_sql(self, expression: exp.Array) -> str:
        # The PG hint establishes NUMERIC, not a fabricated source typmod.
        # Only finite domains with a provable target width/scale are portable.
        widths = {exp.DType.SMALLINT: 5, exp.DType.INT: 10, exp.DType.BIGINT: 19}

        def number_input(value: exp.Expr) -> tuple[str, int, int] | None:
            literal = value.this if isinstance(value, exp.Neg) else value
            if literal.is_number and len(literal.name) <= 4096:
                try:
                    number = Decimal(literal.name)
                except InvalidOperation:
                    return None
                if not number.is_finite():
                    return None
                _, digits, exponent = number.as_tuple()
                exponent = t.cast(int, exponent)
                if abs(exponent) > 10000:
                    return None
                scale = max(-exponent, 0)
                integers = max(len(digits) + exponent, 0) if number else 0
                if integers + scale <= 76:
                    return (
                        ("-" if isinstance(value, exp.Neg) else "") + format(number, "f"),
                        integers,
                        scale,
                    )
            return None

        def bounds(value: exp.Expr) -> tuple[int, int | None] | None:
            value = value.unnest() if isinstance(value, exp.Paren) else value
            if isinstance(value, exp.Array):
                children = [bounds(child) for child in value.expressions]
                if children and all(child is not None for child in children):
                    known = t.cast(list[tuple[int, int | None]], children)
                    scales = {scale for _, scale in known if scale is not None}
                    # PG retains per-element NUMERIC display scales. A fixed
                    # Decimal array must not silently add observable trailing zeros.
                    if len(scales) <= 1:
                        return max(width for width, _ in known), next(iter(scales), None)
            elif (number := number_input(value)) is not None:
                return number[1], number[2]
            elif type(value) is exp.Cast:
                if (width := widths.get(value.to.this)) is not None:
                    return width, 0
            elif (
                value.is_leaf()
                and not isinstance(value, (exp.Func, exp.Literal))
                and annotate_types(
                    exp.Coalesce(this=value.copy(), expressions=[exp.Literal.number(1)]),
                    dialect="postgres",
                ).is_type(exp.DType.INT)
            ):
                # Unknown leaf type, not a precomputed NULL result: preserve the
                # expression and let the runtime CAST handle its value.
                return 0, None
            return None

        resolved = bounds(expression)
        integers, scale = resolved or (77, 0)
        scale = scale or 0
        precision = max(integers + scale, 1)
        if precision > 76:
            self.unsupported("Doris cannot preserve this PostgreSQL NUMERIC array domain")
            return self.func("ARRAY", *expression.expressions)
        dtype = exp.DataType(
            this=exp.DType.DECIMAL,
            expressions=[
                exp.DataTypeParam(this=exp.Literal.number(precision)),
                exp.DataTypeParam(this=exp.Literal.number(scale)),
            ],
        )

        def build(array: exp.Array) -> exp.Expr:
            inputs = []
            for value in array.expressions:
                literal = value.unnest() if isinstance(value, exp.Paren) else value
                if isinstance(literal, exp.Array):
                    inputs.append(build(literal))
                elif (number := number_input(literal)) is not None:
                    inputs.append(exp.Literal.string(number[0]))
                else:
                    inputs.append(exp.cast(value.copy(), exp.DType.TEXT))
            return exp.Array(expressions=inputs)

        def convert(value: exp.Expr, element: exp.DataType, depth: int = 0) -> exp.Expr:
            # A constant ARRAY CAST can round Decimal through DOUBLE in Doris's
            # frontend. Nested Decimal constructors can also narrow to Decimal128.
            # Construct nested text inputs first, then parse inside runtime lambdas.
            parameter = exp.to_identifier(
                "_pg_numeric_input" if not depth else f"_pg_numeric_input_{depth}"
            )
            return exp.func(
                "ARRAY_MAP",
                exp.Lambda(
                    this=convert(parameter, element.expressions[0], depth + 1)
                    if element.is_type(exp.DType.ARRAY)
                    else exp.cast(parameter, dtype.copy()),
                    expressions=[parameter],
                ),
                value,
            )

        return self.sql(convert(build(expression), expression.args["element_type"]))

    def _coerce_array_input(self, expression: exp.Expr, dtype: exp.DataType) -> exp.Expr:
        literal = expression
        while isinstance(literal, exp.Paren):
            literal = literal.this
        if dtype.is_type(exp.DType.FLOAT, exp.DType.DOUBLE):
            if isinstance(literal, exp.Subquery):
                query = literal.this
                if (
                    isinstance(query, exp.Select)
                    and len(query.expressions) == 1
                    and not any(value for key, value in query.args.items() if key != "expressions")
                ):
                    projection = query.expressions[0]
                    value = self._coerce_array_input(projection.unalias(), dtype)
                    if value != projection.unalias():
                        result = literal.copy()
                        if isinstance(projection, exp.Alias):
                            projection = projection.copy()
                            projection.set("this", value)
                        else:
                            projection = value
                        result.this.set("expressions", [projection])
                        return result
                return expression.copy()
            if isinstance(literal, exp.Neg):
                value = self._coerce_array_input(literal.this, dtype)
                return exp.Neg(this=value) if value != literal.this else expression.copy()
            casts: list[exp.DataType | None] = []
            value = literal
            while True:
                if type(value) is exp.Cast and value.to.is_type(exp.DType.FLOAT, exp.DType.DOUBLE):
                    casts.append(value.to)
                elif isinstance(value, exp.Neg) and not value.is_number:
                    casts.append(None)
                else:
                    break
                value = value.this.unnest()
            if (
                casts
                and (value.is_string or value.is_number)
                and (
                    any(cast is not None and cast.is_type(exp.DType.FLOAT) for cast in casts)
                    or (value.is_string and _PG_HEX_FLOAT_INPUT.fullmatch(value.to_py().strip()))
                )
            ):
                original = (
                    "-" + str(value.this.to_py())
                    if isinstance(value, exp.Neg)
                    else str(value.to_py())
                ).strip(" \t\n\r\f\v")
                normalized = original
                for cast in reversed(casts):
                    if cast is None:
                        normalized = (
                            normalized[1:] if normalized.startswith("-") else "-" + normalized
                        )
                        continue
                    rounded = self._array_float_input(normalized, cast)
                    if rounded is None:
                        # Non-finite and unsupported cast inputs retain their
                        # existing generation outside this finite-input rule.
                        return expression.copy()
                    normalized = rounded
                # Preserve each source cast before promotion to the ARRAY's type.
                # Exact inputs can retain their existing CASTs unchanged.
                try:
                    unchanged = Decimal(normalized) == Decimal(original)
                except InvalidOperation:
                    unchanged = False
                if not unchanged:
                    return exp.cast(exp.Literal.string(normalized), exp.DType.DOUBLE)
            elif (
                not isinstance(value, exp.Query)
                and not value.is_string
                and not value.is_number
                and casts
                and next((cast for cast in reversed(casts) if cast is not None), None)
                == exp.DataType.build(exp.DType.DOUBLE)
                and any(cast is not None and cast.is_type(exp.DType.FLOAT) for cast in casts)
            ):
                result = value.copy()
                previous = None
                for cast in reversed(casts):
                    if cast is None:
                        result = exp.Neg(this=result)
                        continue
                    if cast.is_type(exp.DType.FLOAT) and previous == exp.DType.DOUBLE:
                        parameter = exp.to_identifier("_pg_real_input")
                        result = exp.Bracket(
                            this=exp.Anonymous(
                                this="ARRAY_MAP",
                                expressions=[
                                    exp.Lambda(
                                        this=exp.cast(parameter, exp.DType.FLOAT),
                                        expressions=[parameter],
                                    ),
                                    exp.Array(expressions=[result]),
                                ],
                            ),
                            expressions=[exp.Literal.number(1)],
                            offset=self.dialect.INDEX_OFFSET,
                        )
                    else:
                        result = exp.cast(result, cast.copy())
                    previous = cast.this
                return result
        if not literal.is_string or dtype.is_type(exp.DType.TEXT):
            return expression.copy()

        value = literal.to_py().strip(" \t\n\r\f\v")
        normalized = None
        bits = {exp.DType.SMALLINT: 16, exp.DType.INT: 32, exp.DType.BIGINT: 64}.get(dtype.this)
        if bits and _PG_INTEGER_INPUT.fullmatch(value):
            unsigned = value.lstrip("+-").lower().replace("_", "")
            base = {"0x": 16, "0o": 8, "0b": 2}.get(unsigned[:2], 10)
            digits = (unsigned if base == 10 else unsigned[2:]).lstrip("0") or "0"
            # Discard leading zeros before parsing; even a binary integer needs
            # at most `bits` digits, avoiding Python's huge decimal input limit.
            if len(digits) <= bits:
                number = int(digits, base) * (-1 if value.startswith("-") else 1)
                if -(2 ** (bits - 1)) <= number < 2 ** (bits - 1):
                    normalized = str(number)
        elif dtype.is_type(exp.DType.BOOLEAN) and value:
            matches = {
                result
                for result, inputs in (
                    ("true", ("true", "yes", "on", "1")),
                    ("false", ("false", "no", "off", "0")),
                )
                if any(word.startswith(value.lower()) for word in inputs)
            }
            if len(matches) == 1:
                normalized = matches.pop()
        elif dtype.is_type(exp.DType.FLOAT, exp.DType.DOUBLE):
            normalized = self._array_float_input(value, dtype)

        if normalized is None:
            self.unsupported("Doris cannot preserve this PostgreSQL array input conversion")
        else:
            literal = literal.copy()
            literal.set("this", normalized)
        return exp.cast(
            literal.copy(),
            exp.DType.DOUBLE if dtype.is_type(exp.DType.FLOAT) else dtype.copy(),
        )

    def _array_float_input(self, value: str, dtype: exp.DataType) -> str | None:
        # Round input directly to the source IEEE format: Doris can
        # round through DOUBLE before storing FLOAT, unlike PostgreSQL strtof.
        # Bound work before constructing the exact ratio for extreme exponents.
        if len(value) > 4096:
            return None
        if re.fullmatch(r"[+-]?(?:nan|inf(?:inity)?)", value, re.IGNORECASE | re.ASCII):
            return (
                "NaN"
                if value.lstrip("+-").lower() == "nan"
                else ("-Infinity" if value.startswith("-") else "Infinity")
            )
        precision, minimum, maximum, decimal_minimum, decimal_maximum = (
            (24, -149, 127, -46, 38)
            if dtype.is_type(exp.DType.FLOAT)
            else (53, -1074, 1023, -324, 308)
        )
        hexadecimal = _PG_HEX_FLOAT_INPUT.fullmatch(value)
        if hexadecimal:
            sign, mantissa, power = hexadecimal.groups()
            power = power or "0"
            if len(power.lstrip("+-").lstrip("0")) > 5 or abs(int(power)) > 20000:
                return None
            fraction = mantissa.partition(".")[2]
            shift = int(power) - 4 * len(fraction)
            numerator = int(mantissa.replace(".", ""), 16) << max(0, shift)
            denominator = 1 << max(0, -shift)
            negative = sign == "-"
        elif _PG_DECIMAL_FLOAT_INPUT.fullmatch(value):
            try:
                number = Decimal(value)
            except InvalidOperation:
                return None
            if number and not decimal_minimum <= number.adjusted() <= decimal_maximum:
                return None
            numerator, denominator = number.copy_abs().as_integer_ratio()
            negative = number.is_signed()
        else:
            return None
        if not numerator:
            return "-0" if negative else "0"
        exponent = numerator.bit_length() - denominator.bit_length()
        if (numerator << max(0, -exponent)) < (denominator << max(0, exponent)):
            exponent -= 1
        shift = max(exponent - precision + 1, minimum)
        scaled_denominator = denominator << max(0, shift)
        significand, remainder = divmod(numerator << max(0, -shift), scaled_denominator)
        if 2 * remainder > scaled_denominator or (
            2 * remainder == scaled_denominator and significand % 2
        ):
            significand += 1
        if not significand or significand.bit_length() + shift - 1 > maximum:
            return None
        integer = significand << shift if shift >= 0 else significand * 5 ** (-shift)
        decimal = Decimal((int(negative), tuple(map(int, str(integer))), min(shift, 0)))
        return format(decimal, "f").rstrip("0").rstrip(".") if shift < 0 else str(decimal)

    def match_sql(self, expression: exp.Match) -> str:
        kind = expression.text("kind")
        sql = self.binary(expression, f"MATCH_{kind}" if kind else "MATCH")
        analyzer = self.sql(expression, "analyzer")
        return f"{sql} USING ANALYZER {analyzer}" if analyzer else sql

    ARRAY_SIZE_NAME = "ARRAY_SIZE"
    LAST_DAY_SUPPORTS_DATE_PART = False
    NULL_ORDERING_SUPPORTED = True
    SUPPORTS_ALTER_COLUMN_NULLABILITY = False
    VARCHAR_REQUIRES_SIZE = False
    WITH_PROPERTIES_PREFIX = "PROPERTIES"
    RENAME_TABLE_WITH_DB = False
    UPDATE_STATEMENT_SUPPORTS_FROM = True

    TYPE_MAPPING = {
        **MySQLGenerator.TYPE_MAPPING,
        exp.DType.TEXT: "STRING",
        exp.DType.TIMESTAMP: "DATETIME",
        exp.DType.TIMESTAMPTZ: "DATETIME",
    }

    PROPERTIES_LOCATION = {
        **MySQLGenerator.PROPERTIES_LOCATION,
        exp.UniqueKeyProperty: exp.Properties.Location.POST_SCHEMA,
        exp.PartitionedByProperty: exp.Properties.Location.POST_SCHEMA,
        exp.BuildProperty: exp.Properties.Location.POST_SCHEMA,
        exp.Property: exp.Properties.Location.POST_WITH,
    }

    CAST_MAPPING: t.ClassVar[dict[exp.DType, str]] = {}
    TIMESTAMP_FUNC_TYPES: t.ClassVar[set[exp.DType]] = set()

    TRANSFORMS = {
        **{k: v for k, v in MySQLGenerator.TRANSFORMS.items() if k != exp.ArrayAgg},
        exp.AddMonths: rename_func("MONTHS_ADD"),
        exp.ApproxDistinct: approx_count_distinct_sql,
        exp.ArgMax: rename_func("MAX_BY"),
        exp.ArgMin: rename_func("MIN_BY"),
        exp.ArrayToString: rename_func("ARRAY_JOIN"),
        exp.ArrayUniqueAgg: rename_func("COLLECT_SET"),
        exp.CurrentDate: lambda self, _: self.func("CURRENT_DATE"),
        exp.CurrentTimestamp: lambda self, _: self.func("NOW"),
        exp.CovarSamp: _sample_aggregate_sql,
        exp.DateTrunc: lambda self, e: self.func(
            "DATE_TRUNC", e.this, weekstart_unit_to_str(self, e)
        ),
        exp.EuclideanDistance: rename_func("L2_DISTANCE"),
        exp.GroupConcat: lambda self, e: self.func(
            "GROUP_CONCAT", e.this, e.args.get("separator") or exp.Literal.string(",")
        ),
        exp.JSONExtractScalar: lambda self, e: self.func("JSON_EXTRACT", e.this, e.expression),
        exp.Lag: _lag_lead_sql,
        exp.Lead: _lag_lead_sql,
        exp.Map: rename_func("ARRAY_MAP"),
        exp.Property: property_sql,
        exp.RegexpLike: rename_func("REGEXP"),
        exp.RegexpSplit: rename_func("SPLIT_BY_STRING"),
        exp.SchemaCommentProperty: lambda self, e: self.naked_property(e),
        exp.Split: rename_func("SPLIT_BY_STRING"),
        exp.StringToArray: rename_func("SPLIT_BY_STRING"),
        exp.StrToUnix: lambda self, e: self.func("UNIX_TIMESTAMP", e.this, self.format_time(e)),
        exp.Stddev: _sample_aggregate_sql,
        exp.StddevSamp: _sample_aggregate_sql,
        exp.TimeStrToDate: rename_func("TO_DATE"),
        exp.TsOrDsAdd: lambda self, e: self.func("DATE_ADD", e.this, e.expression),
        exp.TsOrDsToDate: lambda self, e: self.func("TO_DATE", e.this),
        exp.TimeToUnix: rename_func("UNIX_TIMESTAMP"),
        exp.TimestampTrunc: lambda self, e: self.func(
            "DATE_TRUNC", e.this, weekstart_unit_to_str(self, e)
        ),
        exp.UnixToStr: lambda self, e: self.func(
            "FROM_UNIXTIME", e.this, time_format("doris")(self, e)
        ),
        exp.UnixToTime: rename_func("FROM_UNIXTIME"),
        exp.Variance: _sample_aggregate_sql,
    }

    # https://github.com/apache/doris/blob/e4f41dbf1ec03f5937fdeba2ee1454a20254015b/fe/fe-core/src/main/antlr4/org/apache/doris/nereids/DorisLexer.g4#L93
    RESERVED_KEYWORDS = {
        "account_lock",
        "account_unlock",
        "add",
        "adddate",
        "admin",
        "after",
        "agg_state",
        "aggregate",
        "alias",
        "all",
        "alter",
        "analyze",
        "analyzed",
        "analyzer",
        "and",
        "anti",
        "append",
        "array",
        "array_range",
        "as",
        "asc",
        "asof",
        "at",
        "authors",
        "auto",
        "auto_increment",
        "backend",
        "backends",
        "backup",
        "begin",
        "belong",
        "between",
        "bigint",
        "bin",
        "binary",
        "binlog",
        "bitand",
        "bitmap",
        "bitmap_union",
        "bitor",
        "bitxor",
        "blob",
        "boolean",
        "both",
        "brief",
        "broker",
        "buckets",
        "build",
        "builtin",
        "bulk",
        "by",
        "cached",
        "call",
        "cancel",
        "case",
        "cast",
        "catalog",
        "catalogs",
        "chain",
        "char",
        "char_filter",
        "character",
        "charset",
        "check",
        "clean",
        "cluster",
        "clusters",
        "collate",
        "collation",
        "collect",
        "column",
        "columns",
        "comment",
        "commit",
        "committed",
        "compact",
        "complete",
        "config",
        "connection",
        "connection_id",
        "consistent",
        "constraint",
        "constraints",
        "convert",
        "copy",
        "count",
        "create",
        "creation",
        "cron",
        "cross",
        "cube",
        "current",
        "current_catalog",
        "current_date",
        "current_time",
        "current_timestamp",
        "current_user",
        "data",
        "database",
        "databases",
        "date",
        "date_add",
        "date_ceil",
        "date_diff",
        "date_floor",
        "date_sub",
        "dateadd",
        "datediff",
        "datetime",
        "datetimev2",
        "datev2",
        "datetimev1",
        "datev1",
        "day",
        "days_add",
        "days_sub",
        "decimal",
        "decimalv2",
        "decimalv3",
        "decommission",
        "default",
        "deferred",
        "delete",
        "demand",
        "desc",
        "describe",
        "diagnose",
        "disk",
        "distinct",
        "distinctpc",
        "distinctpcsa",
        "distributed",
        "distribution",
        "div",
        "do",
        "doris_internal_table_id",
        "double",
        "drop",
        "dropp",
        "dual",
        "dump",
        "duplicate",
        "dynamic",
        "else",
        "enable",
        "encryptkey",
        "encryptkeys",
        "end",
        "ends",
        "engine",
        "engines",
        "enter",
        "errors",
        "events",
        "every",
        "except",
        "exclude",
        "execute",
        "exists",
        "expired",
        "explain",
        "export",
        "extended",
        "external",
        "extract",
        "failed_login_attempts",
        "false",
        "fast",
        "feature",
        "fields",
        "file",
        "filter",
        "first",
        "float",
        "follower",
        "following",
        "for",
        "foreign",
        "force",
        "format",
        "free",
        "from",
        "frontend",
        "frontends",
        "full",
        "function",
        "functions",
        "generic",
        "global",
        "grant",
        "grants",
        "graph",
        "group",
        "grouping",
        "groups",
        "hash",
        "having",
        "hdfs",
        "help",
        "histogram",
        "hll",
        "hll_union",
        "hostname",
        "hour",
        "hub",
        "identified",
        "if",
        "ignore",
        "immediate",
        "in",
        "incremental",
        "index",
        "indexes",
        "infile",
        "inner",
        "insert",
        "install",
        "int",
        "integer",
        "intermediate",
        "intersect",
        "interval",
        "into",
        "inverted",
        "ipv4",
        "ipv6",
        "is",
        "is_not_null_pred",
        "is_null_pred",
        "isnull",
        "isolation",
        "job",
        "jobs",
        "join",
        "json",
        "jsonb",
        "key",
        "keys",
        "kill",
        "label",
        "largeint",
        "last",
        "lateral",
        "layout",
        "ldap",
        "ldap_admin_password",
        "leading",
        "left",
        "less",
        "level",
        "like",
        "limit",
        "lines",
        "link",
        "list",
        "load",
        "local",
        "localtime",
        "localtimestamp",
        "location",
        "lock",
        "logical",
        "low_priority",
        "manual",
        "map",
        "match",
        "match_all",
        "match_any",
        "match_condition",
        "match_phrase",
        "match_phrase_edge",
        "match_phrase_prefix",
        "match_regexp",
        "materialized",
        "max",
        "maxvalue",
        "memo",
        "merge",
        "migrate",
        "migrations",
        "min",
        "minus",
        "minute",
        "modify",
        "month",
        "mtmv",
        "name",
        "names",
        "natural",
        "negative",
        "never",
        "next",
        "ngram_bf",
        "no",
        "no_use_mv",
        "non_nullable",
        "not",
        "null",
        "nulls",
        "observer",
        "of",
        "offset",
        "on",
        "only",
        "open",
        "optimized",
        "or",
        "order",
        "outer",
        "outfile",
        "over",
        "overwrite",
        "parameter",
        "parsed",
        "partition",
        "partitions",
        "password",
        "password_expire",
        "password_history",
        "password_lock_time",
        "password_reuse",
        "path",
        "pause",
        "percent",
        "period",
        "permissive",
        "physical",
        "plan",
        "play",
        "process",
        "plugin",
        "plugins",
        "policy",
        "preceding",
        "prepare",
        "primary",
        "proc",
        "procedure",
        "processlist",
        "profile",
        "properties",
        "property",
        "quantile_state",
        "quantile_union",
        "query",
        "quota",
        "random",
        "range",
        "read",
        "real",
        "rebalance",
        "recover",
        "recycle",
        "refresh",
        "references",
        "regexp",
        "release",
        "rename",
        "repair",
        "repeatable",
        "replace",
        "replace_if_not_null",
        "replica",
        "repositories",
        "repository",
        "resource",
        "resources",
        "restore",
        "restrictive",
        "resume",
        "returns",
        "revoke",
        "rewritten",
        "right",
        "rlike",
        "role",
        "roles",
        "rollback",
        "rollup",
        "routine",
        "row",
        "rows",
        "s3",
        "sample",
        "schedule",
        "scheduler",
        "schema",
        "schemas",
        "second",
        "select",
        "semi",
        "sequence",
        "serializable",
        "session",
        "set",
        "sets",
        "shape",
        "show",
        "signed",
        "skew",
        "smallint",
        "snapshot",
        "soname",
        "split",
        "sql_block_rule",
        "start",
        "starts",
        "stats",
        "status",
        "stop",
        "storage",
        "stream",
        "streaming",
        "string",
        "struct",
        "subdate",
        "sum",
        "superuser",
        "switch",
        "sync",
        "system",
        "table",
        "tables",
        "tablesample",
        "tablet",
        "tablets",
        "task",
        "tasks",
        "temporary",
        "terminated",
        "text",
        "than",
        "then",
        "time",
        "timestamp",
        "timestampadd",
        "timestampdiff",
        "tinyint",
        "to",
        "token_filter",
        "tokenizer",
        "trailing",
        "transaction",
        "trash",
        "tree",
        "triggers",
        "trim",
        "true",
        "truncate",
        "try_cast",
        "type",
        "type_cast",
        "types",
        "unbounded",
        "uncommitted",
        "uninstall",
        "union",
        "unique",
        "unlock",
        "unsigned",
        "update",
        "use",
        "use_mv",
        "user",
        "using",
        "value",
        "values",
        "varchar",
        "variables",
        "variant",
        "vault",
        "verbose",
        "version",
        "view",
        "warnings",
        "week",
        "when",
        "where",
        "whitelist",
        "with",
        "work",
        "workload",
        "write",
        "xor",
        "year",
    }

    def uniquekeyproperty_sql(
        self, expression: exp.UniqueKeyProperty, prefix: str = "UNIQUE KEY"
    ) -> str:
        create_stmt = expression.find_ancestor(exp.Create)
        if create_stmt and create_stmt.args["properties"].find(exp.MaterializedProperty):
            return super().uniquekeyproperty_sql(expression, prefix="KEY")

        return super().uniquekeyproperty_sql(expression)

    def partitionrange_sql(self, expression: exp.PartitionRange) -> str:
        name = self.sql(expression, "this")
        values = expression.expressions

        if len(values) != 1:
            # Multiple values: use VALUES [ ... )
            if values and isinstance(values[0], list):
                values_sql = ", ".join(
                    f"({', '.join(self.sql(v) for v in inner)})" for inner in values
                )
            else:
                values_sql = ", ".join(f"({self.sql(v)})" for v in values)

            return f"PARTITION {name} VALUES [{values_sql})"

        return f"PARTITION {name} VALUES LESS THAN ({self.sql(values[0])})"

    def partitionbyrangepropertydynamic_sql(
        self, expression: exp.PartitionByRangePropertyDynamic
    ) -> str:
        # Generates: FROM ("start") TO ("end") INTERVAL N UNIT
        start = self.sql(expression, "start")
        end = self.sql(expression, "end")
        every = expression.args.get("every")

        if every:
            number = self.sql(every, "this")
            interval = f"INTERVAL {number} {self.sql(every, 'unit')}"
        else:
            interval = ""

        return f"FROM ({start}) TO ({end}) {interval}"

    def partitionedbyproperty_sql(self, expression: exp.PartitionedByProperty) -> str:
        this = expression.this
        if isinstance(this, exp.Schema):
            return f"PARTITION BY ({self.expressions(this, flat=True)})"
        return f"PARTITION BY ({self.sql(this)})"
