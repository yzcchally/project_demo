"""Narrow equality for task-bound native Amap search captions.

This is not a place-name alias resolver. It never removes 地铁站, road names,
gates, campuses, whitespace or arbitrary parentheses.
"""


def task_search_text_matches(actual, expected, city=""):
    if not isinstance(actual, str) or not isinstance(expected, str) or not actual or not expected:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in actual + expected):
        return False
    def station_form(value):
        return value.replace("(地铁站)", "地铁站").replace("（地铁站）", "地铁站")
    actual, expected = station_form(actual), station_form(expected)
    if actual == expected:
        return True
    # Strip at most one exact administrative city prefix. A bare city word
    # may begin the POI itself (广州塔), so it cannot be removed from a name.
    if isinstance(city, str) and city.endswith("市"):
        actual = actual.removeprefix(city)
        expected = expected.removeprefix(city)
    return actual == expected
