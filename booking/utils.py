import qrcode
from datetime import datetime
from cryptography.fernet import Fernet
from config import secrets, redis_client


def validate_lease(reservation_id, spot_id, user_id):
    try:
        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
        if not lease_data:
            return False

        return (lease_data.get('user_id') == str(user_id) and
                lease_data.get('spot_id') == str(spot_id))
    except Exception as e:
        print(f"Lease validation error: {str(e)}")
        return False


def calculate_price(startTime, endTime, spotPricePerHour):
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)


def generate_qr_code(new_booking_id):
    key = secrets["FERNET_KEY"]
    cipher = Fernet(key.encode())
    encrypted = cipher.encrypt(str(new_booking_id).encode()).decode()

    img = qrcode.make(encrypted)
    img.save(f"static/qr_codes/{new_booking_id}.png")
