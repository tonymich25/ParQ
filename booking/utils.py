# booking/utils.py
from config import db, ParkingSpot
from booking.redis import redis_get
from datetime import datetime


def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):
    lease_key = f"spot_lease:{spot.id}"
    current_lease = redis_get(redis_client, lease_key)

    if current_lease:
        return 1

    from config import Booking
    return Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()


def calculate_price(startTime, endTime, spotPricePerHour):
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)