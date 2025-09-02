import uuid
from sqlalchemy import select, update
from flask import current_app
from datetime import datetime, timedelta
from config import redis_client, db, ParkingSpot, Booking
from booking.redis_utils import redis_acquire_lease, redis_renew_lease, redis_delete_lease
from booking.idempotency import check_idempotency, store_idempotency_result
from zoneinfo import ZoneInfo
from booking.utils import is_spot_available, calculate_price, emit_to_relevant_rooms_about_booking

def acquire_lease(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240, reservation_id=None):
    def ensure_24h_format(time_str):
        try:
            if ':' in time_str:
                parts = time_str.split(':')
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                return f"{hour:02d}:{minute:02d}"
        except (ValueError, TypeError):
            pass
        return time_str

    start_time_24h = ensure_24h_format(start_time)
    end_time_24h = ensure_24h_format(end_time)

    lease_key = f"spot_lease:{spot_id}_{booking_date}"

    if reservation_id is not None:
        existing_lease = redis_client.get(lease_key)
        if existing_lease and existing_lease == reservation_id:
            print(f"Idempotent success - reservation {reservation_id} already exists")
            return reservation_id

    if reservation_id is None:
        reservation_id = str(uuid.uuid4())

    print(f"Attempting to acquire lease for spot {spot_id}")
    print(f"Key: {lease_key}")
    print(f"Reservation ID: {reservation_id}")
    print(f"User: {user_id}, Lot: {parking_lot_id}")
    print(f"Date: {booking_date}, Time: {start_time}-{end_time}")

    lease_data = {
        'user_id': str(user_id),
        'spot_id': str(spot_id),
        'parking_lot_id': str(parking_lot_id),
        'booking_date': booking_date,
        'start_time': start_time_24h,
        'end_time': end_time_24h,
        'created_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat()
    }

    try:
        redis_client.hset(f"lease_data:{reservation_id}", mapping=lease_data)
        redis_client.expire(f"lease_data:{reservation_id}", ttl + 60)
    except Exception as e:
        print(f"Failed to store lease metadata: {str(e)}")
        return None

    result = redis_acquire_lease(redis_client, lease_key, reservation_id, ttl)
    print(f"   Redis acquire result: {result}")

    if not result:
        print(f"FAILED - Could not acquire lease for spot {spot_id}")
        redis_client.delete(f"lease_data:{reservation_id}")
        return None

    print(f"SUCCESS - Lease acquired for spot {spot_id}")
    return reservation_id


def confirm_booking(reservation_id, spot_id, user_id, booking_data, idempotency_key=None):
    current_app.logger.info(f"confirm_booking called - reservation: {reservation_id}, spot: {spot_id}, user: {user_id}")

    if idempotency_key:
        try:
            current_app.logger.info(f"Checking idempotency key: {idempotency_key}")
            cached_response, is_cached = check_idempotency(idempotency_key)
            if is_cached:
                current_app.logger.info(f"Idempotent response found: {cached_response}")
                return cached_response, 200
        except Exception as e:
            current_app.logger.error(f"Idempotency check failed: {str(e)}")
            idempotency_key = None

    try:
        with db.session.begin_nested():
            current_app.logger.info(f"🔒 Acquiring database lock for spot: {spot_id}")
            spot = db.session.execute(
                select(ParkingSpot)
                .where(ParkingSpot.id == spot_id)
                .with_for_update()
            ).scalar_one()
            current_app.logger.info(f"Database lock acquired for spot: {spot_id}")

            current_app.logger.info(f"Validating lease: {reservation_id}")
            lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
            if not lease_data:
                current_app.logger.error(f"Lease not found: {reservation_id}")
                result = {"status": "error", "message": "Lease not found"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            lease_user_id = lease_data.get(b'user_id', b'').decode() if b'user_id' in lease_data else lease_data.get('user_id', '')
            lease_spot_id = lease_data.get(b'spot_id', b'').decode() if b'spot_id' in lease_data else lease_data.get('spot_id', '')

            current_app.logger.info(f"Lease validation - user: {lease_user_id} vs {user_id}, spot: {lease_spot_id} vs {spot_id}")

            if (lease_user_id != str(user_id) or lease_spot_id != str(spot_id)):
                current_app.logger.error(f"Lease validation failed - mismatch")
                result = {"status": "error", "message": "Lease validation failed"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            booking_date = booking_data['booking_date']
            lease_key = f"spot_lease:{spot_id}_{booking_date}"
            current_lease = redis_client.get(lease_key)
            if current_lease and isinstance(current_lease, bytes):
                current_lease = current_lease.decode('utf-8')

            current_app.logger.info(f"Lease consistency check - key: {lease_key}, current: {current_lease}, expected: {reservation_id}")

            if not current_lease or current_lease != reservation_id:
                current_app.logger.warning(f"Lease lost, attempting to renew: {lease_key}, {reservation_id}")
                success = redis_acquire_lease(redis_client, lease_key, reservation_id, 240)
                if not success:
                    current_app.logger.error(f"Lease lost and could not be renewed")
                    result = {"status": "error", "message": "Lease lost and could not be renewed"}
                    if idempotency_key:
                        store_idempotency_result(idempotency_key, result)
                    return result, 409
                else:
                    current_app.logger.info("Lease successfully renewed")

            start_time = datetime.strptime(booking_data['start_time'], '%H:%M').time()
            end_time = datetime.strptime(booking_data['end_time'], '%H:%M').time()

            current_app.logger.info(f"Checking spot availability for {spot_id} at {start_time}-{end_time}")

            conflict_count = Booking.query.filter(
                Booking.spot_id == spot.id,
                Booking.parking_lot_id == booking_data['parking_lot_id'],
                Booking.bookingDate == booking_data['booking_date'],
                Booking.startTime < end_time,
                Booking.endTime > start_time
            ).count()

            current_app.logger.info(f"Atomic availability check - conflicts: {conflict_count}")

            if conflict_count > 0:
                current_app.logger.error(f"Spot no longer available: {spot_id} (conflicts: {conflict_count})")
                result = {"status": "error", "message": "Spot no longer available"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            current_app.logger.info("Creating booking record")
            booking = create_booking_from_data(spot, user_id, booking_data)
            db.session.add(booking)

        current_app.logger.info(f"🧹 Cleaning up lease after successful booking: {reservation_id}")
        lease_key = f"spot_lease:{spot_id}_{booking_data['booking_date']}"
        redis_delete_lease(redis_client, lease_key, reservation_id)
        redis_client.delete(f"lease_data:{reservation_id}")

        result = {"status": "success", "booking_id": booking.id}
        current_app.logger.info(f"Booking confirmed successfully! ID: {booking.id}")

        if idempotency_key:
            store_idempotency_result(idempotency_key, result)
        return result, 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Booking confirmation failed: {str(e)}", exc_info=True)
        result = {"status": "error", "message": "Internal server error"}
        if idempotency_key:
            store_idempotency_result(idempotency_key, result)
        return result, 500

def create_booking_from_data(spot, user_id, booking_data):
    start_time = datetime.strptime(booking_data['start_time'], '%H:%M').time()
    end_time = datetime.strptime(booking_data['end_time'], '%H:%M').time()

    return Booking(
        userid=user_id,
        parking_lot_id=int(booking_data['parking_lot_id']),
        spot_id=spot.id,
        bookingDate=datetime.strptime(booking_data['booking_date'], '%Y-%m-%d').date(),
        startTime=start_time,
        endTime=end_time,
        amount=calculate_price(
            start_time,
            end_time,
            spot.pricePerHour
        )
    )
