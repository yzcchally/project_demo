"""Search-caption equality is narrower than POI aliases and never input authority."""
import pytest

from perception.search_text import task_search_text_matches


@pytest.mark.parametrize('actual,expected',[
    ('体育西路(地铁站)','体育西路地铁站'),
    ('体育西路（地铁站）','体育西路(地铁站)'),
    ('广州市体育西路(地铁站)','体育西路地铁站'),
    ('体育西路(地铁站)','广州市体育西路地铁站'),
    ('广州市广州塔（地铁站）','广州塔地铁站'),
    ('广州塔(地铁站)','广州市广州塔地铁站'),
    ('华南理工大学(五山校区)','华南理工大学(五山校区)'),
])
def test_only_station_parenthesis_format_and_exact_city_prefix_are_equivalent(actual,expected):
    assert task_search_text_matches(actual,expected,'广州市')
    assert task_search_text_matches(expected,actual,'广州市')


@pytest.mark.parametrize('actual,expected,city',[
    ('体育西路','体育西路地铁站','广州市'),
    ('广州塔','广州塔地铁站','广州市'),
    ('体育西路(公交站)','体育西路地铁站','广州市'),
    ('体育西路(地铁站)G口','体育西路地铁站','广州市'),
    ('体育西路(地铁站)','广州塔地铁站','广州市'),
    ('北京市体育西路(地铁站)','体育西路地铁站','广州市'),
    ('广东省广州市体育西路(地铁站)','体育西路地铁站','广州市'),
    ('广州市广州市体育西路(地铁站)','体育西路地铁站','广州市'),
    ('体育西路 (地铁站)','体育西路地铁站','广州市'),
    ('体育西路（地铁站)','体育西路地铁站','广州市'),
    ('体育西路(地铁站)推荐','体育西路地铁站','广州市'),
    ('华南理工大学（五山校区）','华南理工大学(五山校区)','广州市'),
    ('塔地铁站','广州塔地铁站','广州'),
    ('\n体育西路(地铁站)','体育西路地铁站','广州市'),
    (None,'体育西路地铁站','广州市'),
    (123,'体育西路地铁站','广州市'),
])
def test_no_road_landmark_gate_other_city_campus_or_whitespace_alias_is_invented(actual,expected,city):
    assert not task_search_text_matches(actual,expected,city)
