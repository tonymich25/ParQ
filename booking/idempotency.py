from flask import jsonify, current_app
from config import db, IdempotencyKey

def check_idempotency(key):
    if not key:
        return None, False

    existing = IdempotencyKey.query.get(key)
    if existing:
        return existing.result, True
    return None, False

def store_idempotency_result(key, result):
    if not key:
        return

    try:
        idempotency_record = IdempotencyKey(
            key=key,
            result=result
        )
        db.session.add(idempotency_record)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to store idempotency key: {str(e)}")
