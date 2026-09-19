"""다양성 보정 전용. 계획서 §7 이 '한 정류장에서만 8개 후보가 나올 때'를 요구했다."""

from nowbus.core.ranking import Weights, rank, score_plan
from nowbus.models import Catch, Plan, Route, Stop


def stop(sid):
    return Stop(sid, sid, f"정류장{sid}", 37.5, 127.0)


def plan(stop_id, route_id, total, catch=Catch.SAFE, walk_to=4.0, walk_from=3.0):
    return Plan(
        board=stop(stop_id),
        alight=stop("D"),
        route=Route(route_id, route_id),
        walk_to_board_min=walk_to,
        walk_from_alight_min=walk_from,
        eta_min=5.0,
        ride_min=total - walk_to - walk_from,
        total_min=total,
        margin_min=1.0,
        catch=catch,
        score=0.0,
    )


class TestScore:
    def test_tight_gets_penalty(self):
        w = Weights(tight=3.0, walk=0.0)
        safe = score_plan(plan("A", "1", 30, Catch.SAFE), w)
        tight = score_plan(plan("A", "1", 30, Catch.TIGHT), w)
        assert tight - safe == 3.0

    def test_walking_gets_penalty(self):
        w = Weights(tight=0.0, walk=0.2)
        near = score_plan(plan("A", "1", 30, walk_to=2.0, walk_from=1.0), w)
        far = score_plan(plan("A", "1", 30, walk_to=10.0, walk_from=8.0), w)
        assert far > near


class TestDiversity:
    def test_caps_one_stop_at_two(self):
        """한 정류장에서 8개가 나와도 5장을 그걸로 채우지 않는다.

        후보들이 서로 비슷할 때(30~37분 vs 34~37분) 상한이 작동한다.
        """
        plans = [plan("A", str(i), 30 + i) for i in range(8)]
        plans += [
            plan("B", "b1", 34),
            plan("C", "c1", 35),
            plan("D", "d1", 36),
            plan("E", "e1", 37),
        ]
        out = rank(plans, Weights(tight=0, walk=0))
        assert len(out) == 5
        assert sum(1 for p in out if p.board.stop_id == "A") == 2
        assert len({p.board.stop_id for p in out}) == 4

    def test_many_dominant_candidates_all_survive(self):
        """A 의 후보 여럿이 전부 압도적이면 상한을 넘어서 올라간다.

        더 빠른 버스를 '같은 정류장'이라는 이유로 숨기지 않는다. 대안이 사라지는
        것은 min_distinct_stops 가 막는다.
        """
        plans = [plan("A", str(i), 30 + i) for i in range(8)]
        plans += [plan("B", "b1", 50), plan("C", "c1", 52)]
        out = rank(plans, Weights(tight=0, walk=0))
        assert sum(1 for p in out if p.board.stop_id == "A") > 2
        assert len({p.board.stop_id for p in out}) >= 2  # 대안은 남는다

    def test_falls_back_when_cap_starves_the_list(self):
        """상한 때문에 5개를 못 채우면 제한을 풀고 채운다."""
        plans = [plan("A", str(i), 30 + i) for i in range(8)]
        out = rank(plans, Weights(tight=0, walk=0))
        assert len(out) == 5
        assert all(p.board.stop_id == "A" for p in out)

    def test_guarantees_two_distinct_stops(self):
        """A 가 압도적이어도 대안 정류장 하나는 남는다."""
        plans = [plan("A", str(i), 10 + i) for i in range(6)]
        plans.append(plan("B", "b1", 99))
        out = rank(plans, Weights(tight=0, walk=0), min_distinct_stops=2)
        assert len({p.board.stop_id for p in out}) >= 2

    def test_dominant_candidate_is_not_pushed_out(self):
        """계획서 §10-4: 압도적으로 좋은 후보를 '같은 정류장'이라고 밀어내면 안 된다.

        A 정류장에 20/21/22분, 다른 정류장은 50분대뿐이라면 A 의 세 번째(22분)가
        50분짜리보다 낫다. max_per_stop 은 동점 근처에서만 작동해야 한다.
        """
        plans = [plan("A", "a1", 20), plan("A", "a2", 21), plan("A", "a3", 22)]
        plans += [plan("B", "b1", 50), plan("C", "c1", 52)]
        out = rank(plans, Weights(tight=0, walk=0), dominance_min=5.0)
        assert sum(1 for p in out if p.board.stop_id == "A") == 3

    def test_cap_applies_when_scores_are_close(self):
        """반대 경우. 차이가 작으면 상한이 정상 작동해 다양성을 확보한다."""
        plans = [plan("A", "a1", 20), plan("A", "a2", 21), plan("A", "a3", 22)]
        plans += [plan("B", "b1", 23), plan("C", "c1", 24), plan("D", "d1", 25)]
        out = rank(plans, Weights(tight=0, walk=0), dominance_min=5.0)
        assert sum(1 for p in out if p.board.stop_id == "A") == 2
        assert len({p.board.stop_id for p in out}) == 4


class TestBasics:
    def test_dedupes_same_stop_and_route(self):
        """같은 정류장의 같은 노선이 1차·2차로 두 번 들어오면 좋은 쪽만 남는다."""
        plans = [plan("A", "146", 30), plan("A", "146", 45), plan("B", "360", 32)]
        out = rank(plans, Weights(tight=0, walk=0))
        assert len(out) == 2
        assert out[0].total_min == 30

    def test_sorted_by_score(self):
        plans = [plan("A", "1", 40), plan("B", "2", 20), plan("C", "3", 30)]
        out = rank(plans, Weights(tight=0, walk=0))
        assert [p.total_min for p in out] == [20, 30, 40]

    def test_score_is_written_back(self):
        out = rank([plan("A", "1", 30, Catch.TIGHT)], Weights(tight=3.0, walk=0.0))
        assert out[0].score == 33.0

    def test_empty(self):
        assert rank([], Weights()) == []

    def test_fewer_than_top_n(self):
        assert len(rank([plan("A", "1", 30)], Weights())) == 1
