import uuid
import redis
from datetime import datetime
from flask import current_app, request
from flask_login import current_user
from flask_socketio import emit

from booking.booking import booking_service
from booking.booking.booking_service import acquire_lease_safe
from booking.emit_utils.emit import emit_to_relevant_rooms_about_booking
from booking.pending_bookings.pending_bookings_db import store_pending_booking, delete_pending_booking
from booking.redis.redis_utils import redis_hget, redis_hset, redis_safe_release_lease
from booking.stripe.create_stripe_session import create_stripe_session, create_stripe_session_direct
from booking.utils import calculate_price
from config import ParkingSpot, redis_client, db, Booking, PendingBooking, socketio


@socketio.on('book_spot')
def book_spot(data):
    try:
        current_app.logger.info(f"book_spot event received: {data}")

        # Check Redis health
        redis_available = socketio.server.manager.redis_available

        if redis_available:
            # Try Redis-based booking first
            try:
                return process_redis_booking(data, request.sid)
            except (redis.exceptions.ConnectionError, Exception) as e:
                if isinstance(e, redis.exceptions.ConnectionError):
                    booking_service.redis_circuit_open = True
                    current_app.logger.warning("Redis connection failed - opening circuit breaker")

                current_app.logger.warning(f"Redis booking failed, falling back to direct: {str(e)}")
                return process_direct_booking(data, request.sid)
        else:
            # Redis is down, use direct booking
            current_app.logger.info("Redis unavailable - using direct booking")
            return process_direct_booking(data, request.sid)

    except Exception as e:
        current_app.logger.error(f"book_spot error: {str(e)}", exc_info=True)
        emit('booking_failed', {'reason': 'Booking failed'}, room=request.sid)



def process_redis_booking(data, sid):
    """Process booking using Redis lease system"""
    try:
        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'}, room=sid)
            return

        conn_data = redis_hget(redis_client, "active_connections", sid) or {}
        existing_reservation_id = conn_data.get('reservation_id')

        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        # Redis connection check
        redis_client.ping()

        reservation_id = acquire_lease_safe(
            spot_id=spot.id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            booking_date=data.get('bookingDate'),
            start_time=start_time_str,
            end_time=end_time_str,
            reservation_id=existing_reservation_id
        )

        if not reservation_id:
            emit('booking_failed', {'reason': 'Spot already taken'}, room=sid)
            return

        conn_data['reservation_id'] = reservation_id
        redis_hset(redis_client, "active_connections", sid, conn_data)

        emit_to_relevant_rooms_about_booking(
            spot,
            data.get('bookingDate'),
            False,
            True,
            datetime.strptime(start_time_str, "%H:%M").time(),
            datetime.strptime(end_time_str, "%H:%M").time()
        )

        from booking.non_redis_cross_instance_worker.cross_instance_manager import broadcast_spot_update
        broadcast_spot_update(
            spot,
            data.get('bookingDate'),
            False,
            datetime.strptime(start_time_str, "%H:%M").time(),
            datetime.strptime(end_time_str, "%H:%M").time()
        )

        with db.session.begin_nested():
            checkout_url = create_stripe_session(
                data, start_time_str, end_time_str, spot, reservation_id
            )

            if not checkout_url:
                # Cleanup on failure
                lease_key = f"spot_lease:{spot.id}_{data.get('bookingDate')}"
                redis_safe_release_lease(redis_client, lease_key, reservation_id)
                emit_to_relevant_rooms_about_booking(
                    spot, data.get('bookingDate'), True, False
                )
                emit('booking_failed', {'reason': 'Payment system error'}, room=sid)
                return

            emit('payment_redirect', {'url': checkout_url}, room=sid)

    except redis.exceptions.ConnectionError:
        raise
    except Exception as e:
        current_app.logger.error(f"Redis booking error: {str(e)}")
        emit('booking_failed', {'reason': str(e)}, room=sid)



def process_direct_booking(data, sid):
    """Handle direct booking when Redis is down - with WebSocket updates"""
    try:
        current_app.logger.info("Processing direct booking fallback")

        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'})
            return

        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        booking_date = datetime.strptime(data.get('bookingDate'), '%Y-%m-%d').date()

        emit_to_relevant_rooms_about_booking(
            spot,
            data.get('bookingDate'),
            False,
            False,
            start_time,
            end_time
        )

        from booking.non_redis_cross_instance_worker.cross_instance_manager import broadcast_spot_update
        broadcast_spot_update(
            spot,
            data.get('bookingDate'),
            False,
            start_time,
            end_time
        )

        # Check for conflicts after emitting
        with db.session.begin_nested():
            conflict_count = Booking.query.filter(
                Booking.spot_id == int(data.get('spotId')),
                Booking.parking_lot_id == int(data.get('parkingLotId')),
                Booking.bookingDate == booking_date,
                Booking.startTime < end_time,
                Booking.endTime > start_time
            ).count()

            if conflict_count > 0:
                current_app.logger.error(f"Spot {data.get('spotId')} already booked")
                emit_to_relevant_rooms_about_booking(
                    spot,
                    data.get('bookingDate'),
                    True,
                    False
                )
                emit('booking_failed', {'reason': 'This spot was just booked by someone else'})
                return

            # Check for conflicting pending bookings
            conflicting_pending = PendingBooking.query.filter(
                PendingBooking.spot_id == int(data.get('spotId')),
                PendingBooking.parking_lot_id == int(data.get('parkingLotId')),
                PendingBooking.booking_date == booking_date,
                PendingBooking.start_time < end_time,
                PendingBooking.end_time > start_time
            ).first()

            if conflicting_pending:
                current_app.logger.warning(
                    f"Spot {data.get('spotId')} has pending booking: {conflicting_pending.reservation_id}")
                emit_to_relevant_rooms_about_booking(
                    spot,
                    data.get('bookingDate'),
                    True,
                    False
                )
                emit('booking_failed',
                     {'reason': 'This spot is currently being booked by someone else. Please try again in a moment.'})
                return

        amount = calculate_price(start_time, end_time, spot.pricePerHour)
        reservation_id = str(uuid.uuid4())

        storage_success = store_pending_booking(
            reservation_id=reservation_id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            spot_id=data.get('spotId'),
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            amount=amount
        )

        if not storage_success:
            success = emit_to_relevant_rooms_about_booking(
                spot,
                data.get('bookingDate'),
                False,
                False
            )

            if not success:
                current_app.logger.warning("Cross-instance broadcast failed - only updating local instance")
            emit('booking_failed', {'reason': 'Failed to process booking'})
            return

        checkout_url = create_stripe_session_direct(
            data,
            start_time_str,
            end_time_str,
            spot,
            reservation_id
        )

        if not checkout_url:
            # Since booking failed emit_utils spot is available
            success = emit_to_relevant_rooms_about_booking(
                spot,
                data.get('bookingDate'),
                False,
                False
            )

            if not success:
                current_app.logger.warning("Cross-instance broadcast failed - only updating local instance")
            delete_pending_booking(reservation_id)
            emit('booking_failed', {'reason': 'Payment system error'})
            return

        emit('payment_redirect', {'url': checkout_url})

    except Exception as e:
        current_app.logger.error(f"Direct booking error: {str(e)}")
        # Since booking failed emit_utils spot is available
        if 'spot' in locals():
            emit_to_relevant_rooms_about_booking(
                spot,
                data.get('bookingDate'),
                True,
                False
            )
        emit('booking_failed', {'reason': 'Booking failed. Please try again.'})
