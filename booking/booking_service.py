import uuid
from sqlalchemy import select, update
from flask import current_app
from datetime import datetime, timedelta
from config import db, ParkingSpot, redis_client, Outbox, Booking, SpotLease
from booking.redis import redis_acquire_lease, redis_renew_lease, redis_delete_lease, redis_get
from idempotency import check_idempotency, store_idempotency_result
from zoneinfo import ZoneInfo
from booking.utils import is_spot_available, calculate_price

def acquire_lease(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240, reservation_id=None):
    import traceback
    print(" acquire_lease() called from:")
    traceback.print_stack(limit=3)

    lease_key = f"spot_lease:{spot_id}"

    # Check for existing reservation first
    if reservation_id is None:
        # Check if there's already a lease for this spot by the same user
        existing_lease = redis_get(redis_client, lease_key)
        if existing_lease:
            # Verify this lease belongs to the same user (optional security check)
            return None  # Or handle accordingly
        reservation_id = str(uuid.uuid4())
    else:
        # Check if this specific reservation ID already exists
        existing_lease = redis_get(redis_client, lease_key)
        if existing_lease and existing_lease == reservation_id:
            return reservation_id  # Idempotent success

    print(f"🎯 Attempting to acquire lease for spot {spot_id}")
    print(f"   Key: {lease_key}")
    print(f"   Reservation ID: {reservation_id}")
    # ... rest of function ...

def acquire_lease(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240):

    import traceback
    print(" acquire_lease() called from:")
    traceback.print_stack(limit=3)

    reservation_id = str(uuid.uuid4())


    if reservation_id is None:
        reservation_id = str(uuid.uuid4())

    lease_key = f"spot_lease:{spot_id}"

    # Check if we already have this reservation
    existing_lease = redis_get(redis_client, lease_key)
    if existing_lease and existing_lease == reservation_id:
        return reservation_id



    print(f"🎯 Attempting to acquire lease for spot {spot_id}")
    print(f"   Key: {lease_key}")
    print(f"   Reservation ID: {reservation_id}")
    print(f"   User: {user_id}, Lot: {parking_lot_id}")
    print(f"   Date: {booking_date}, Time: {start_time}-{end_time}")

    result = redis_acquire_lease(redis_client, lease_key, reservation_id, ttl)
    print(f"   Redis acquire result: {result}")

    if not result:
        print(f"❌ FAILED - Could not acquire lease for spot {spot_id}")
        return None

    print(f"✅ SUCCESS - Lease acquired for spot {spot_id}")


    if not result:
        return None
    held_until = datetime.now(ZoneInfo("Europe/Nicosia")) + timedelta(seconds=ttl)
    try:
        lease = SpotLease(
            spot_id=spot_id,
            user_id=user_id,
            reservation_id=reservation_id,
            parking_lot_id=parking_lot_id,
            booking_date=datetime.strptime(booking_date, '%Y-%m-%d').date(),
            start_time=datetime.strptime(start_time, '%H:%M').time(),
            end_time=datetime.strptime(end_time, '%H:%M').time(),
            held_until=held_until
        )
        db.session.add(lease)
        db.session.commit()
        return reservation_id
    except Exception as e:
        redis_delete_lease(redis_client, lease_key, reservation_id)  # ADD redis_client
        db.session.rollback()
        current_app.logger.error(f"Failed to store lease: {str(e)}")
        return None
def acquire_lease(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240):
    """Acquire a lease with all necessary data for the worker"""
    reservation_id = str(uuid.uuid4())
    lease_key = f"spot_lease:{spot_id}"

    # Try to acquire lease in Redis
    result = redis_acquire_lease(lease_key, reservation_id, ttl)
    if not result:
        return None

    # Store lease details in database for worker
    held_until = datetime.now(ZoneInfo("Europe/Nicosia")) + timedelta(seconds=ttl)

    try:
        lease = SpotLease(
            spot_id=spot_id,
            user_id=user_id,
            reservation_id=reservation_id,
            parking_lot_id=parking_lot_id,
            booking_date=datetime.strptime(booking_date, '%Y-%m-%d').date(),
            start_time=datetime.strptime(start_time, '%H:%M').time(),
            end_time=datetime.strptime(end_time, '%H:%M').time(),
            held_until=held_until
        )
        db.session.add(lease)
        db.session.commit()
        return reservation_id
    except Exception as e:
        # Clean up Redis lease if DB fails
        redis_delete_lease(lease_key, reservation_id)
        db.session.rollback()
        current_app.logger.error(f"Failed to store lease: {str(e)}")
        return None


def confirm_booking(reservation_id, spot_id, user_id, booking_data, idempotency_key=None):
    """
    Confirm a booking with full idempotency support
    Returns: (result_dict, status_code)
    """
    # Check idempotency first
    if idempotency_key:
        cached_response, is_cached = check_idempotency(idempotency_key)
        if is_cached:
            return cached_response, 200

    try:
        with db.session.begin_nested():
            # 1. Lock the spot row for update
            spot = db.session.execute(
                select(ParkingSpot)
                .where(ParkingSpot.id == spot_id)
                .with_for_update()
            ).scalar_one()

            # 2. Verify Redis lease still exists and belongs to this reservation
            lease_key = f"spot_lease:{spot_id}"
            current_lease = redis_get(lease_key)
            if current_lease and current_lease != reservation_id:
                result = {"status": "error", "message": "Lease lost"}
                store_idempotency_result(idempotency_key, result)
                return result, 409

            # 3. Check if spot is still available
            if not is_spot_available(spot, booking_data['parking_lot_id'], booking_data['booking_date'],
                                     booking_data['start_time'], booking_data['end_time']):
                result = {"status": "error", "message": "Spot no longer available"}
                store_idempotency_result(idempotency_key, result)
                return result, 409

            # 4. Create booking and outbox event in same transaction
            booking = create_booking_from_data(spot, user_id, booking_data)
            db.session.add(booking)

            outbox_event = Outbox(
                event_type='booking_created',
                payload={
                    'booking_id': booking.id,
                    'spot_id': spot_id,
                    'user_id': user_id,
                    'booking_data': booking_data
                }
            )
            db.session.add(outbox_event)

        # 5. After successful commit, clean up Redis lease
        redis_delete_lease(lease_key, reservation_id)

        # 6. Store successful result for idempotency
        result = {"status": "success", "booking_id": booking.id}
        store_idempotency_result(idempotency_key, result)

        return result, 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Booking confirmation failed: {str(e)}")
        result = {"status": "error", "message": "Internal server error"}
        store_idempotency_result(idempotency_key, result)
        return result, 500


def create_booking_from_data(spot, user_id, booking_data):
    """Create a Booking object from booking data"""
    return Booking(
        userid=user_id,
        parking_lot_id=booking_data['parking_lot_id'],
        spot_id=spot.id,
        bookingDate=datetime.strptime(booking_data['booking_date'], '%Y-%m-%d').date(),
        startTime=datetime.strptime(booking_data['start_time'], '%H:%M').time(),
        endTime=datetime.strptime(booking_data['end_time'], '%H:%M').time(),
        amount=calculate_price(
            datetime.strptime(booking_data['start_time'], '%H:%M').time(),
            datetime.strptime(booking_data['end_time'], '%H:%M').time(),
            spot.pricePerHour
        )
    )