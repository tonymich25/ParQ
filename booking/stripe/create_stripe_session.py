import stripe
import datetime
from flask import url_for, current_app
from flask_login import current_user
from booking.redis.redis_utils import redis_hset
from config import redis_client


def create_stripe_session(data, start_time_str, end_time_str, spot, reservation_id):
    """Create Stripe checkout session - mark lease as payment in progress"""
    try:
        lease_data_key = f"lease_data:{reservation_id}"
        redis_hset(redis_client, lease_data_key, 'payment_context', 'true')
        redis_client.expire(lease_data_key, 600)

        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        hours = (end_time.hour - start_time.hour) + (end_time.minute - start_time.minute) / 60
        price = max(round(hours * 2 * 100), 50)

        success_url = f"{url_for('booking_bp.payment_success', _external=True)}?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = url_for('booking_bp.booking_form', _external=True)

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Parking Spot #{spot.spotNumber}',
                        'description': f'{data.get("bookingDate")} {start_time_str}-{end_time_str}'
                    },
                    'unit_amount': price,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                'reservation_id': reservation_id,
                'spot_id': str(spot.id),
                'parking_lot_id': data.get('parkingLotId'),
                'booking_date': data.get('bookingDate'),
                'start_time': start_time_str,
                'end_time': end_time_str,
                'user_id': str(current_user.get_id())
            }
        )

        redis_client.hset(lease_data_key, 'stripe_session_id', session.id)

        return session.url

    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}")
        return None



def create_stripe_session_direct(data, start_time_str, end_time_str, spot, reservation_id):
    """Create Stripe checkout session for direct booking (no Redis lease)"""
    try:
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        hours = (end_time.hour - start_time.hour) + (end_time.minute - start_time.minute) / 60
        price = max(round(hours * spot.pricePerHour * 100), 50)

        success_url = f"{url_for('booking_bp.payment_success_direct', _external=True)}?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = url_for('booking_bp.booking_form', _external=True)

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Parking Spot #{spot.spotNumber}',
                        'description': f'{data.get("bookingDate")} {start_time_str}-{end_time_str}'
                    },
                    'unit_amount': price,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                'reservation_id': reservation_id,
                'spot_id': str(spot.id),
                'parking_lot_id': data.get('parkingLotId'),
                'booking_date': data.get('bookingDate'),
                'start_time': start_time_str,
                'end_time': end_time_str,
                'user_id': str(current_user.get_id()),
                'direct_booking': 'true'
            }
        )

        return session.url

    except Exception as e:
        current_app.logger.error(f"Direct Stripe session creation failed: {str(e)}")
        return None