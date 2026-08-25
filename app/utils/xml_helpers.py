"""
Small helpers shared by the TWB/TWBX XML parsers.
"""

from __future__ import annotations

import re
from typing import Optional
from xml.etree.ElementTree import Element

LOD_KEYWORDS = ("{ FIXED", "{FIXED", "{ INCLUDE", "{INCLUDE", "{ EXCLUDE", "{EXCLUDE")
TABLE_CALC_FUNCTIONS = (
    "RUNNING_",
    "WINDOW_",
    "INDEX(",
    "RANK(",
    "RANK_",
    "TOTAL(",
    "LOOKUP(",
    "PREVIOUS_VALUE(",
    "FIRST(",
    "LAST(",
)


def text_or_default(element: Optional[Element], default: str = "") -> str:
    if element is None or element.text is None:
        return default
    return element.text.strip()


def attr_or_default(element: Optional[Element], attr: str, default: str = "") -> str:
    if element is None:
        return default
    return element.attrib.get(attr, default)


def is_lod_expression(formula: str) -> bool:
    if not formula:
        return False
    upper = formula.upper()
    return any(keyword.upper() in upper for keyword in LOD_KEYWORDS)


def is_table_calculation(formula: str) -> bool:
    if not formula:
        return False
    upper = formula.upper()
    return any(fn in upper for fn in TABLE_CALC_FUNCTIONS)


def is_custom_sql(relation_element: Element) -> bool:
    return relation_element.attrib.get("type") == "text"


_FIELD_NAMESPACE_PREFIX = re.compile(r"^(usr|none):")
_FIELD_ROLE_SUFFIX = re.compile(r":[a-z]{2,4}:\d+$")


def clean_field_reference(raw: str) -> str:
    """Normalize a Tableau field/calculation reference into something
    worth showing a user.

    References can be a simple bracketed name ("[Sales]"), or a
    compound, namespaced, internally-keyed one produced for federated
    datasources and calculated fields, e.g.:

        [federated.12p60851wjjcxx1brr7rf13bgtp0].[usr:Calculation_0014397469077547:nk:1]

    The datasource id and the "usr:"/"none:" namespace and trailing
    ":nk:1"-style role suffix are Tableau-internal plumbing, never
    something a person recognizes -- take the last bracket-qualified
    segment (the actual field/calculation reference) and strip that
    plumbing off it.
    """
    if not raw:
        return raw

    segments = re.findall(r"\[([^\[\]]+)\]", raw)
    value = segments[-1] if segments else raw.strip("[]")

    value = _FIELD_NAMESPACE_PREFIX.sub("", value)
    value = _FIELD_ROLE_SUFFIX.sub("", value)

    return value.strip()