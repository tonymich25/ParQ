import uuid
from sqlalchemy import select, update
from flask import current_app
from datetime import datetime, timedelta
from config import redis_client, db, ParkingSpot, Booking, socketio  # REMOVE SpotLease
from booking.redis_utils import redis_renew_lease, redis_delete_lease, redis_delete_lease, redis_acquire_lease
from booking.idempotency import check_idempotency, store_idempotency_result
from zoneinfo import ZoneInfo
from booking.utils import is_spot_available, calculate_price, emit_to_relevant_rooms_about_booking
import redis
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(redis.RedisError),
    reraise=True
)
def acquire_lease_safe(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240,
                       reservation_id=None):
    """Try to acquire a lease with retries."""

    # 🎯 USE YOUR RESILIENTREDISMANAGER'S BUILT-IN CIRCUIT BREAKER
    redis_available = socketio.server.manager.redis_available

    if not redis_available:
        raise redis.RedisError("Redis circuit open - using fallback mode")

    try:
        # ✅ Use the high-level acquire_lease function
        success = acquire_lease(
            spot_id=spot_id,
            user_id=user_id,
            parking_lot_id=parking_lot_id,
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            ttl=ttl,
            reservation_id=reservation_id
        )
        return success
    except redis.RedisError as e:
        current_app.logger.warning(f"🔴 Redis error: {e}")
        raise e


def acquire_lease(spot_id, user_id, parking_lot_id, booking_date, start_time, end_time, ttl=240, reservation_id=None):
    # 🎯 FIX: Use consistent key format with date included

    def ensure_24h_format(time_str):
        try:
            if ':' in time_str:
                parts = time_str.split(':')
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                # Return in consistent 24h format
                return f"{hour:02d}:{minute:02d}"
        except (ValueError, TypeError):
            pass
        return time_str

    start_time_24h = ensure_24h_format(start_time)
    end_time_24h = ensure_24h_format(end_time)

    lease_key = f"spot_lease:{spot_id}_{booking_date}"

    # Idempotency check FIRST
    if reservation_id is not None:
        existing_lease = redis_client.get(lease_key)
        if existing_lease and existing_lease == reservation_id:
            print(f"🔄 Idempotent success - reservation {reservation_id} already exists")
            return reservation_id

    # Generate new ID if needed
    if reservation_id is None:
        reservation_id = str(uuid.uuid4())

    print(f"🎯 Attempting to acquire lease for spot {spot_id}")
    print(f"   Key: {lease_key}")
    print(f"   Reservation ID: {reservation_id}")
    print(f"   User: {user_id}, Lot: {parking_lot_id}")
    print(f"   Date: {booking_date}, Time: {start_time}-{end_time}")

    # 🎯 FIX: Store lease metadata FIRST before acquiring the lease
    lease_data = {
        'user_id': str(user_id),
        'spot_id': str(spot_id),
        'parking_lot_id': str(parking_lot_id),
        'booking_date': booking_date,
        'start_time': start_time_24h,
        'end_time': end_time_24h,
        'created_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat()
    }

    # Store metadata with TTL (slightly longer than lease TTL)
    try:
        redis_client.hset(f"lease_data:{reservation_id}", mapping=lease_data)
        redis_client.expire(f"lease_data:{reservation_id}", ttl + 60)  # 1 minute buffer
    except Exception as e:
        print(f"❌ Failed to store lease metadata: {str(e)}")
        return None

    # Now acquire the lease
    result = redis_acquire_lease(redis_client, lease_key, reservation_id, ttl)
    print(f"   Redis acquire result: {result}")

    if not result:
        print(f"❌ FAILED - Could not acquire lease for spot {spot_id}")
        # Clean up metadata if lease acquisition failed
        redis_client.delete(f"lease_data:{reservation_id}")
        return None

    print(f"✅ SUCCESS - Lease acquired for spot {spot_id}")
    return reservation_id


def confirm_booking(reservation_id, spot_id, user_id, booking_data, idempotency_key=None):
    current_app.logger.info(
        f"🎯 confirm_booking called - reservation: {reservation_id}, spot: {spot_id}, user: {user_id}")

    if idempotency_key:
        try:
            current_app.logger.info(f"🔍 Checking idempotency key: {idempotency_key}")
            cached_response, is_cached = check_idempotency(idempotency_key)
            if is_cached:
                current_app.logger.info(f"✅ Idempotent response found: {cached_response}")
                return cached_response, 200
        except Exception as e:
            current_app.logger.error(f"❌ Idempotency check failed: {str(e)}")
            idempotency_key = None

    lease_key = f"spot_lease:{spot_id}_{booking_data['booking_date']}"

    # ✅ VALIDATE existing lease instead of re-acquiring
    current_app.logger.info(f"🔍 Validating existing lease: {lease_key}")
    current_lease = redis_client.get(lease_key)

    if current_lease is None:
        current_app.logger.error(f"❌ Lease not found or expired: {lease_key}")
        result = {"status": "error", "message": "Lease expired or not found"}
        if idempotency_key:
            store_idempotency_result(idempotency_key, result)
        return result, 409

    if isinstance(current_lease, bytes):
        current_lease = current_lease.decode('utf-8')

    current_app.logger.info(f"🔍 Lease validation - current: {current_lease}, expected: {reservation_id}")

    if current_lease != reservation_id:
        current_app.logger.error(f"❌ Lease validation failed - mismatch")
        result = {"status": "error", "message": "Lease validation failed - spot taken by another user"}
        if idempotency_key:
            store_idempotency_result(idempotency_key, result)
        return result, 409

    # ✅ Lease validation successful - proceed with booking
    current_app.logger.info(f"✅ Lease validation successful for reservation: {reservation_id}")

    try:
        with db.session.begin_nested():
            # 🎯 ATOMIC LOCKING - Get spot with FOR UPDATE first to prevent race conditions
            current_app.logger.info(f"🔒 Acquiring database lock for spot: {spot_id}")
            spot = db.session.execute(
                select(ParkingSpot)
                .where(ParkingSpot.id == spot_id)
                .with_for_update()  # This locks the row for update
            ).scalar_one()
            current_app.logger.info(f"✅ Database lock acquired for spot: {spot_id}")

            # 🎯 CRITICAL: Check Redis lease consistency again while holding the database lock
            current_lease_after_lock = redis_client.get(lease_key)
            if current_lease_after_lock and isinstance(current_lease_after_lock, bytes):
                current_lease_after_lock = current_lease_after_lock.decode('utf-8')

            current_app.logger.info(
                f"🔍 Lease consistency check after lock - key: {lease_key}, current: {current_lease_after_lock}, expected: {reservation_id}")

            if not current_lease_after_lock or current_lease_after_lock != reservation_id:
                current_app.logger.warning(f"⚠️ Lease lost after acquiring lock, attempting to renew...")
                success = redis_renew_lease(redis_client, lease_key, reservation_id, 240)
                if not success:
                    current_app.logger.error(f"❌ Lease lost and could not be renewed")
                    result = {"status": "error", "message": "Lease lost and could not be renewed"}
                    if idempotency_key:
                        store_idempotency_result(idempotency_key, result)
                    return result, 409
                else:
                    current_app.logger.info("✅ Lease successfully renewed")

            # Validate lease from Redis metadata
            current_app.logger.info(f"🔍 Validating lease metadata: {reservation_id}")
            lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
            if not lease_data:
                current_app.logger.error(f"❌ Lease metadata not found: {reservation_id}")
                result = {"status": "error", "message": "Lease metadata not found"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            # Handle Redis bytes data properly
            lease_user_id = lease_data.get(b'user_id', b'').decode() if b'user_id' in lease_data else lease_data.get(
                'user_id', '')
            lease_spot_id = lease_data.get(b'spot_id', b'').decode() if b'spot_id' in lease_data else lease_data.get(
                'spot_id', '')

            current_app.logger.info(
                f"🔍 Lease metadata validation - user: {lease_user_id} vs {user_id}, spot: {lease_spot_id} vs {spot_id}")

            # Validate lease ownership
            if (lease_user_id != str(user_id) or lease_spot_id != str(spot_id)):
                current_app.logger.error(f"❌ Lease metadata validation failed - mismatch")
                result = {"status": "error", "message": "Lease metadata validation failed"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            # 🎯 ATOMIC AVAILABILITY CHECK - Check availability while holding the lock
            start_time = datetime.strptime(booking_data['start_time'], '%H:%M').time()
            end_time = datetime.strptime(booking_data['end_time'], '%H:%M').time()

            current_app.logger.info(f"🔍 Checking spot availability for {spot_id} at {start_time}-{end_time}")

            # Check database conflicts while holding the lock
            conflict_count = Booking.query.filter(
                Booking.spot_id == spot.id,
                Booking.parking_lot_id == booking_data['parking_lot_id'],
                Booking.bookingDate == booking_data['booking_date'],
                Booking.startTime < end_time,
                Booking.endTime > start_time
            ).count()

            current_app.logger.info(f"🔍 Atomic availability check - conflicts: {conflict_count}")

            if conflict_count > 0:
                current_app.logger.error(f"❌ Spot no longer available: {spot_id} (conflicts: {conflict_count})")
                result = {"status": "error", "message": "Spot no longer available"}
                if idempotency_key:
                    store_idempotency_result(idempotency_key, result)
                return result, 409

            # Create booking
            current_app.logger.info("✅ Creating booking record")
            booking = create_booking_from_data(spot, user_id, booking_data)
            db.session.add(booking)

        # Clean up Redis lease after successful booking
        current_app.logger.info(f"🧹 Cleaning up lease after successful booking: {reservation_id}")
        lease_key = f"spot_lease:{spot_id}_{booking_data['booking_date']}"
        redis_delete_lease(redis_client, lease_key, reservation_id)
        redis_client.delete(f"lease_data:{reservation_id}")

        result = {"status": "success", "booking_id": booking.id}
        current_app.logger.info(f"🎉 Booking confirmed successfully! ID: {booking.id}")

        if idempotency_key:
            store_idempotency_result(idempotency_key, result)

        return result, 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"❌ Booking confirmation failed: {str(e)}", exc_info=True)
        result = {"status": "error", "message": "Internal server error"}
        if idempotency_key:
            store_idempotency_result(idempotency_key, result)
        return result, 500




def create_booking_from_data(spot, user_id, booking_data):
    # 🎯 FIX: Calculate price properly
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
