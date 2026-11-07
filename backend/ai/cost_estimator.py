from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CostEstimate:
    amount: Optional[float]
    is_estimated: bool
    label: str


class CostEstimator:

    def estimate_place_cost(
        self,
        category: Optional[str],
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
    ) -> CostEstimate:

        category = (category or "").strip()

        price_min = self._safe_float(price_min)
        price_max = self._safe_float(price_max)

        # -----------------------------------------
        # Có giá thật trong DB
        # -----------------------------------------

        if price_min is not None and price_max is not None:
            amount = (price_min + price_max) / 2

            return CostEstimate(
                amount=amount,
                is_estimated=False,
                label="Giá từ dữ liệu cơ sở dữ liệu",
            )

        if price_min is not None:

            return CostEstimate(
                amount=price_min,
                is_estimated=False,
                label="Giá từ dữ liệu cơ sở dữ liệu",
            )

        if price_max is not None:

            return CostEstimate(
                amount=price_max,
                is_estimated=False,
                label="Giá từ dữ liệu cơ sở dữ liệu",
            )

        return CostEstimate(
            amount=None,
            is_estimated=True,
            label="Chưa có giá được xác thực",
        )

    def estimate_hotel_price(
        self,
        price: Optional[float],
        price_min: Optional[float],
        price_max: Optional[float],
    ) -> CostEstimate:

        price = self._safe_float(price)
        price_min = self._safe_float(price_min)
        price_max = self._safe_float(price_max)

        # -----------------------------------------
        # Có giá DB
        # -----------------------------------------

        if price_min is not None and price_max is not None:

            return CostEstimate(
                amount=(price_min + price_max) / 2,
                is_estimated=False,
                label="Giá phòng từ dữ liệu cơ sở dữ liệu",
            )

        if price_min is not None:

            return CostEstimate(
                amount=price_min,
                is_estimated=False,
                label="Giá phòng từ dữ liệu cơ sở dữ liệu",
            )

        if price_max is not None:

            return CostEstimate(
                amount=price_max,
                is_estimated=False,
                label="Giá phòng từ dữ liệu cơ sở dữ liệu",
            )

        if price is not None:

            return CostEstimate(
                amount=price,
                is_estimated=False,
                label="Giá phòng từ dữ liệu cơ sở dữ liệu",
            )

        # -----------------------------------------
        # Không có giá
        # -----------------------------------------

        return CostEstimate(
            amount=None,
            is_estimated=True,
            label="Chưa có giá phòng được xác thực",
        )

    @staticmethod
    def _safe_float(value):

        if value is None:
            return None

        try:

            number = float(value)

            if number < 0:
                return None

            return number

        except (
            TypeError,
            ValueError,
        ):
            return None
