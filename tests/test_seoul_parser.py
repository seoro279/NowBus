"""저장된 실제 응답으로 파싱만 검증한다. 실 API 호출은 하지 않는다 (계획서 §7)."""

import pytest

from nowbus.providers.seoul import (
    SeoulApiError,
    check_header,
    parse_arrivals,
    parse_headways,
    parse_route_stops,
    parse_stations_by_pos,
)


class TestArrivals:
    def test_parses_both_orders(self, fx):
        arrivals = parse_arrivals(fx("getStationByUid"))
        assert arrivals
        assert {a.order for a in arrivals} == {1, 2}

    def test_eta_is_minutes_from_seconds(self, fx):
        """traTime 은 초 단위다. 340번 1차는 36초 = 0.6분."""
        a = next(
            x
            for x in parse_arrivals(fx("getStationByUid"))
            if x.route_id == "100100055" and x.order == 1
        )
        assert a.eta_min == pytest.approx(36 / 60)

    def test_eta_matches_human_message(self, fx):
        """1남양주 1차는 traTime=1371초, 안내문구는 '23분후'. 반올림하면 일치해야 한다."""
        arrivals = parse_arrivals(fx("getStationByUid"))
        long_wait = [a for a in arrivals if a.eta_min > 20]
        assert long_wait
        assert round(long_wait[0].eta_min) == 23

    def test_congestion_zero_means_unknown(self, fx):
        """혼잡도 0 은 미측정이다. 0 을 '한산함'으로 오해하면 안 된다."""
        arrivals = parse_arrivals(fx("getStationByUid"))
        assert all(a.congestion is None or a.congestion > 0 for a in arrivals)
        assert any(a.congestion == 3 for a in arrivals)

    def test_response_stid_is_not_the_queried_stop(self, fx):
        """응답의 <stId> 를 믿으면 안 된다는 근거.

        arsId=22339(강남역8번출구) 하나로 조회했는데 경기 광역버스 두 건은
        stId=221000178(구리역7번출구)을 달고 온다.
        """
        ids = {a.stop_id for a in parse_arrivals(fx("getStationByUid"))}
        assert len(ids) > 1, "stId 가 정류장마다 다르지 않다면 이 방어는 불필요하다"

    def test_stop_id_is_overridden_by_caller(self, fx):
        """호출자가 아는 정류장 ID 로 덮어써야 route_stop 조인이 어긋나지 않는다."""
        arrivals = parse_arrivals(fx("getStationByUid"), stop_id="121000262")
        assert {a.stop_id for a in arrivals} == {"121000262"}

    def test_headway_from_term(self, fx):
        """1·2차가 모두 MISS 일 때 3차를 추정하는 근거."""
        assert parse_headways(fx("getStationByUid"))["100100055"] == 7.0


class TestStationsByPos:
    def test_skips_non_stopping_stops(self, fx):
        """arsId=0 인 '(미정차)' 정류소는 후보에서 빠져야 한다."""
        stops = parse_stations_by_pos(fx("getStationByPos"))
        # 원본 23건 중 미정차 3건 제외. arsId=0 인 2건 + 정상 arsId 인데 이름이 '(미정차)'인 1건
        assert len(stops) == 20
        assert all("(미정차)" not in s.name for s in stops)
        assert all(s.ars_id not in (None, "", "0") for s in stops)

    def test_keeps_them_when_asked(self, fx):
        assert len(parse_stations_by_pos(fx("getStationByPos"), skip_non_stopping=False)) == 23

    def test_lat_lon_not_swapped(self, fx):
        """gpsY=위도, gpsX=경도. 뒤집히면 정류장이 서해 한복판에 찍힌다."""
        s = parse_stations_by_pos(fx("getStationByPos"))[0]
        assert 37.0 < s.lat < 38.0
        assert 126.0 < s.lon < 128.0


class TestRouteStops:
    def test_row_count_and_seq_range(self, fx):
        rows = parse_route_stops(fx("getStaionByRoute"))
        assert len(rows) == 135
        assert [r.seq for r in rows] == list(range(1, 136))

    def test_seq_unique_across_whole_route(self, fx):
        """seq 가 방향별로 1부터 다시 시작하지 않는다.
        그래서 PK 를 (route_id, seq) 로 둘 수 있다."""
        rows = parse_route_stops(fx("getStaionByRoute"))
        assert len({r.seq for r in rows}) == len(rows)

    def test_direction_is_terminal_name_not_code(self, fx):
        """설계서는 INTEGER 로 잡았지만 실제로는 행선지 종점명 문자열이다."""
        rows = parse_route_stops(fx("getStaionByRoute"))
        assert {r.direction for r in rows} == {"강남역", "상계주공7단지"}

    def test_direction_flips_once_at_turnaround(self, fx):
        """왕복이 하나의 seq 축에 이어 붙어 있고 회차 지점에서 한 번 바뀐다."""
        rows = parse_route_stops(fx("getStaionByRoute"))
        flips = [r.seq for a, r in zip(rows, rows[1:], strict=False) if a.direction != r.direction]
        assert flips == [69]

    def test_stop_id_uses_station_field(self, fx):
        """<station> 이 stop_id 다. <stationNo>(=arsId)를 쓰면 조인이 깨진다."""
        rows = parse_route_stops(fx("getStaionByRoute"))
        gangnam9 = next(r for r in rows if r.stop_name == "강남역9번출구")
        assert gangnam9.stop_id == "121000091"
        assert gangnam9.ars_id == "22167"


def test_nonzero_header_raises():
    bad = (
        "<ServiceResult><msgHeader><headerCd>4</headerCd>"
        "<headerMsg>인증키 오류</headerMsg></msgHeader></ServiceResult>"
    )
    with pytest.raises(SeoulApiError, match="headerCd=4"):
        check_header(bad)
