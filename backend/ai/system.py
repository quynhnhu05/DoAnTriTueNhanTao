from __future__ import annotations

from .recommender import TourismRecommender
from .planner import TripPlanner


def build_system(places):
    recommender = TourismRecommender(places)
    planner = TripPlanner(recommender)
    return recommender, planner
