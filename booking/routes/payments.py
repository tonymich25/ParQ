import stripe
import datetime
from flask import request, current_app, flash, redirect, url_for
from booking.booking.booking_service import confirm_booking
from booking.emit_utils.emit import emit_to_relevant_rooms_about_booking
from booking.pending_bookings.pending_bookings_db import delete_pending_booking
from booking.socket.socket_con_management import disconnect_user
from booking.utils import generate_qr_code
from booking.routes.views import booking_bp
from config import ParkingSpot, Booking, db, PendingBooking


@booking_bp.route('/payment_success', methods=['GET'])
def payment_success():
    session_id = request.args.get('session_id')
    current_app.logger.info(f"payment_success called with session_id: {session_id}")

    if not session_id:
        current_app.logger.error("No session_id provided in payment_success")
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking_bp.booking_form'))

    try:
        current_app.logger.info(f"Retrieving Stripe session: {session_id}")
        session = stripe.checkout.Session.retrieve(session_id)
        current_app.logger.info(f"Stripe session retrieved: {session.id}, status: {session.payment_status}")

        # Extract metadata
        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')
        user_id = session.metadata.get('user_id')

        current_app.logger.info(f"Session metadata - reservation_id: {reservation_id}, spot_id: {spot_id}, "
                              f"parking_lot_id: {parking_lot_id}, booking_date: {booking_date}, "
                              f"start_time: {start_time}, end_time: {end_time}, user_id: {user_id}")

        # Validate all required metadata
        if not all([reservation_id, spot_id, parking_lot_id, booking_date, start_time, end_time, user_id]):
            current_app.logger.error("Missing required metadata in Stripe session")
            flash("Invalid payment session data. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        current_app.logger.info(f"Verifying spot exists: {spot_id}")
        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            current_app.logger.error(f"Spot not found: {spot_id}")
            flash("Invalid spot. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        booking_data = {
            'parking_lot_id': parking_lot_id,
            'booking_date': booking_date,
            'start_time': start_time,
            'end_time': end_time
        }

        # Use idempotency key (Stripe session ID)
        idempotency_key = f"stripe_{session_id}"
        current_app.logger.info(f"Using idempotency key: {idempotency_key}")

        # Confirm the booking with atomic transaction
        current_app.logger.info(f"Attempting to confirm booking for reservation: {reservation_id}")
        result, status_code = confirm_booking(
            reservation_id=reservation_id,
            spot_id=spot_id,
            user_id=user_id,
            booking_data=booking_data,
            idempotency_key=idempotency_key
        )

        current_app.logger.info(f"Booking confirmation result: {result}, status_code: {status_code}")

        if status_code != 200:
            # Booking failed - issue refund
            current_app.logger.error(f"Booking failed with status {status_code}. Issuing refund.")
            try:
                refund = stripe.Refund.create(payment_intent=session.payment_intent)
                current_app.logger.info(f"Refund issued: {refund.id}")
                flash("Booking failed. Refund issued. Please try again.", "error")
            except stripe.error.StripeError as refund_error:
                current_app.logger.error(f"Refund failed: {str(refund_error)}")
                flash("Booking failed. Please contact support for refund.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Booking successful
        booking_id = result.get('booking_id')
        current_app.logger.info(f"Booking successful! Booking ID: {booking_id}")

        if not booking_id:
            current_app.logger.warning("⚠Booking completed but no booking_id returned")
            flash("Booking completed but could not retrieve booking details.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        # Fetch the booking from database to generate QR code
        current_app.logger.info(f"Fetching booking from database: {booking_id}")
        new_booking = Booking.query.get(booking_id)
        if not new_booking:
            current_app.logger.warning(f"⚠Booking not found in database: {booking_id}")
            flash("Booking completed but details not found.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        current_app.logger.info("📱 Generating QR code")
        generate_qr_code(new_booking.id)

        # Disconnect user sockets (but preserve lease until cleanup)
        current_app.logger.info("Disconnecting user sockets")
        disconnect_user(session)

        current_app.logger.info("Payment and booking process completed successfully!")
        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe error in payment_success: {str(e)}", exc_info=True)
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))
    except Exception as e:
        current_app.logger.error(f"Unexpected error in payment_success: {str(e)}", exc_info=True)
        flash("Payment received! If your booking doesn't appear, contact support.", "warning")
        return redirect(url_for('dashboard.dashboard'))



@booking_bp.route('/payment_success_direct', methods=['GET'])
def payment_success_direct():
    """Handle payment success for direct bookings (when Redis is down)"""
    session_id = request.args.get('session_id')
    current_app.logger.info(f"Direct payment success called with session_id: {session_id}")

    try:
        session = stripe.checkout.Session.retrieve(session_id)

        if session.payment_status != 'paid':
            flash("Payment not completed. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Extract metadata
        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        user_id = session.metadata.get('user_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')

        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            current_app.logger.error(f"Spot not found: {spot_id}")
            flash("Invalid spot. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Convert times to proper objects
        start_time_obj = datetime.strptime(start_time, '%H:%M').time()
        end_time_obj = datetime.strptime(end_time, '%H:%M').time()
        booking_date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()

        with db.session.begin_nested():
            # Check for conflicting confirmed bookings first
            conflict_count = Booking.query.filter(
                Booking.spot_id == int(spot_id),
                Booking.parking_lot_id == int(parking_lot_id),
                Booking.bookingDate == booking_date_obj,
                Booking.startTime < end_time_obj,
                Booking.endTime > start_time_obj
            ).count()

            if conflict_count > 0:
                current_app.logger.error(f"Spot {spot_id} already booked by someone else")
                delete_pending_booking(reservation_id)
                # Issue refund since spot is taken
                try:
                    refund = stripe.Refund.create(payment_intent=session.payment_intent)
                    current_app.logger.info(f"Refund issued: {refund.id}")
                except Exception as refund_error:
                    current_app.logger.error(f"Refund failed: {str(refund_error)}")

                flash("This spot was already booked by someone else. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            # Check for conflicting pending bookings from OTHER users
            conflicting_pending = PendingBooking.query.filter(
                PendingBooking.spot_id == int(spot_id),
                PendingBooking.parking_lot_id == int(parking_lot_id),
                PendingBooking.booking_date == booking_date_obj,
                PendingBooking.start_time < end_time_obj,
                PendingBooking.end_time > start_time_obj,
                PendingBooking.reservation_id != reservation_id
            ).first()

            if conflicting_pending:
                current_app.logger.warning(f"Conflict with pending booking: {conflicting_pending.reservation_id}")
                delete_pending_booking(reservation_id)
                try:
                    refund = stripe.Refund.create(payment_intent=session.payment_intent)
                    current_app.logger.info(f"Refund issued: {refund.id}")
                except Exception as refund_error:
                    current_app.logger.error(f"Refund failed: {str(refund_error)}")

                flash("This spot was reserved by someone else while you were paying. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            booking = Booking(
                userid=int(user_id),
                parking_lot_id=int(parking_lot_id),
                spot_id=int(spot_id),
                bookingDate=booking_date_obj,
                startTime=start_time_obj,
                endTime=end_time_obj,
                amount=float(session.amount_total)
            )

            db.session.add(booking)
            db.session.flush()

            generate_qr_code(booking.id)

            delete_pending_booking(reservation_id)

        db.session.commit()

        emit_to_relevant_rooms_about_booking(
            spot,
            booking_date,
            False,
            False
        )

        current_app.logger.info(f"Direct booking completed successfully! Booking ID: {booking.id}")
        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Direct payment success error: {str(e)}", exc_info=True)

        if spot:
            booking_date = session.metadata.get('booking_date') if 'session' in locals() else None
            if booking_date:
                emit_to_relevant_rooms_about_booking(
                    spot,
                    booking_date,
                    True,
                    False
                )

        try:
            refund = stripe.Refund.create(payment_intent=session.payment_intent)
            current_app.logger.info(f"Refund issued due to error: {refund.id}")
        except Exception as refund_error:
            current_app.logger.error(f"Refund failed: {str(refund_error)}")

        flash("Payment received! If your booking doesn't appear, contact support for refund.", "warning")
        return redirect(url_for('dashboard.dashboard'))
