import redis
import datetime
from flask import request, current_app, jsonify
from booking.routes.views import booking_bp
from config import app, socketio, redis_client, ParkingLot, Booking, PendingBooking


def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):
    app.logger.info(
        f"is_spot_available called - spot: {spot.id}, lot: {parkingLotId}, date: {bookingDate}, time: {startTime}-{endTime}")

    # Check Redis health
    redis_available = socketio.server.manager.redis_available

    if redis_available:
        lease_key = f"spot_lease:{spot.id}_{bookingDate}"
        current_lease = redis_client.get(lease_key)
        if current_lease and isinstance(current_lease, bytes):
            current_lease = current_lease.decode('utf-8')

        app.logger.info(f"Lease check - key: {lease_key}, current_lease: {current_lease}")

        if current_lease:
            app.logger.info(f"Spot {spot.id} has active lease: {current_lease}")
            return False  # Spot is leased
    else:
        app.logger.info("Redis unavailable - skipping lease check")

    from config import Booking
    conflict_count = Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()

    app.logger.info(f"Database conflict check - conflicts: {conflict_count}")

    return conflict_count == 0


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    try:
        data = request.get_json()
        current_app.logger.info(f"🔍 DEBUG: Received data: {data}")

        parkingLotId = data.get('parkingLotId')
        startTime_str = data.get('startTime')
        endTime_str = data.get('endTime')
        bookingDate = data.get('bookingDate')

        redis_available = socketio.server.manager.redis_available

        startTime = datetime.strptime(startTime_str, "%H:%M").time()
        endTime = datetime.strptime(endTime_str, "%H:%M").time()

        current_app.logger.info(
            f"DEBUG: Checking lot {parkingLotId}, date {bookingDate}, time {startTime}-{endTime}, Redis: {'✅' if redis_available else '❌'}")

        parkingLot = ParkingLot.query.get(parkingLotId)
        if not parkingLot:
            current_app.logger.error(f"Parking lot not found: {parkingLotId}")
            return jsonify({'error': 'Parking lot not found'}), 404

        allSpots = parkingLot.spots
        current_app.logger.info(f"Found {len(allSpots)} spots for parking lot {parkingLotId}")

        conflicting_bookings = Booking.query.filter(
            Booking.parking_lot_id == parkingLotId,
            Booking.bookingDate == bookingDate,
            Booking.startTime < endTime,
            Booking.endTime > startTime
        ).with_entities(Booking.spot_id).all()

        booked_spot_ids = {b[0] for b in conflicting_bookings}
        current_app.logger.info(f"Booked spot IDs: {booked_spot_ids}")

        leased_spot_ids = set()
        lease_keys_found = []

        if redis_available:
            lease_pattern = f"spot_lease:*_{bookingDate}"
            current_app.logger.info(f"Looking for lease pattern: {lease_pattern}")

            cursor = 0
            try:
                while True:
                    cursor, keys = redis_client.scan(cursor=cursor, match=lease_pattern, count=100)
                    current_app.logger.info(f"SCAN result - cursor: {cursor}, keys: {keys}")

                    for lease_key in keys:
                        if isinstance(lease_key, bytes):
                            lease_key = lease_key.decode('utf-8')

                        lease_keys_found.append(lease_key)
                        current_app.logger.info(f"Processing lease key: {lease_key}")

                        try:
                            key_parts = lease_key.split(':')
                            if len(key_parts) < 2:
                                continue

                            spot_date_parts = key_parts[1].split('_')
                            if len(spot_date_parts) < 2:
                                continue

                            spot_id = spot_date_parts[0]

                            reservation_id = redis_client.get(lease_key)
                            if reservation_id and isinstance(reservation_id, bytes):
                                reservation_id = reservation_id.decode('utf-8')

                            current_app.logger.info(
                                f"Lease {lease_key} -> spot {spot_id}, reservation {reservation_id}")

                            if reservation_id:
                                # Get lease metadata to check time overlap
                                lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                                current_app.logger.info(f"Lease data: {lease_data}")

                                if lease_data:
                                    # Handle Redis bytes data
                                    lease_start_str = lease_data.get(b'start_time',
                                                                     b'').decode() if b'start_time' in lease_data else lease_data.get(
                                        'start_time', '')
                                    lease_end_str = lease_data.get(b'end_time',
                                                                   b'').decode() if b'end_time' in lease_data else lease_data.get(
                                        'end_time', '')

                                    current_app.logger.info(
                                        f"Lease times - start: {lease_start_str}, end: {lease_end_str}")

                                    if lease_start_str and lease_end_str:
                                        lease_start = datetime.strptime(lease_start_str, "%H:%M").time()
                                        lease_end = datetime.strptime(lease_end_str, "%H:%M").time()

                                        # Converting to datetime to handle edge cases
                                        base_date = datetime.today().date()
                                        lease_start_dt = datetime.combine(base_date, lease_start)
                                        lease_end_dt = datetime.combine(base_date, lease_end)
                                        requested_start_dt = datetime.combine(base_date, startTime)
                                        requested_end_dt = datetime.combine(base_date, endTime)

                                        time_overlap = (
                                                (requested_start_dt < lease_end_dt) and
                                                (requested_end_dt > lease_start_dt)
                                        )

                                        app.logger.info(
                                            f"Time overlap check - requested: {startTime}-{endTime}, lease: {lease_start}-{lease_end}, overlap: {time_overlap}")

                                        if time_overlap:
                                            leased_spot_ids.add(spot_id)
                                            current_app.logger.info(
                                                f"Added spot {spot_id} to leased spots due to time overlap")
                        except (IndexError, ValueError, TypeError) as e:
                            current_app.logger.error(f"Error processing lease key {lease_key}: {e}")
                            continue

                    if cursor == 0:
                        break

            except redis.exceptions.ConnectionError:
                current_app.logger.warning("Circuit Breaker: Redis down. Using DB results only.")
                leased_spot_ids = set()

            except Exception as e:
                current_app.logger.error(f"Redis error (using fallback): {e}")
                leased_spot_ids = set()
        else:
            current_app.logger.info("Using fallback mode - skipping Redis lease checks")

        current_app.logger.info(f"Leased spot IDs: {leased_spot_ids}")
        current_app.logger.info(f"All lease keys found: {lease_keys_found}")

        pending_conflicts = PendingBooking.query.filter(
            PendingBooking.parking_lot_id == parkingLotId,
            PendingBooking.booking_date == bookingDate,
            PendingBooking.start_time < endTime,
            PendingBooking.end_time > startTime
        ).with_entities(PendingBooking.spot_id).all()

        pending_spot_ids = {p[0] for p in pending_conflicts}
        current_app.logger.info(f"Pending booking spot IDs: {pending_spot_ids}")

        spots_data = []
        for spot in allSpots:
            is_available = (spot.id not in booked_spot_ids and
                            str(spot.id) not in leased_spot_ids and
                            spot.id not in pending_spot_ids)

            current_app.logger.info(
                f"Spot {spot.id} - available: {is_available} (booked: {spot.id in booked_spot_ids}, leased: {str(spot.id) in leased_spot_ids}, pending: {spot.id in pending_spot_ids})")
            spots_data.append({
                'id': spot.id,
                'spotNumber': spot.spotNumber,
                'svgCoords': spot.svgCoords,
                'is_available': is_available,
                'pricePerHour': spot.pricePerHour
            })

        return jsonify({
            'image_filename': parkingLot.image_filename,
            'spots': spots_data,
            'booked_count': len(booked_spot_ids),
            'leased_count': len(leased_spot_ids),
            'lease_keys_found': lease_keys_found,
            'redis_available': redis_available
        })

    except Exception as e:
        current_app.logger.error(f"Error checking spot availability: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
