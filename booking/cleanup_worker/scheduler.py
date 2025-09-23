from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import current_app
from config import app, PendingBooking, db, ActiveConnection


def init_scheduler():
    """Initialize background scheduler for cleanup tasks"""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        cleanup_expired_pending_bookings,
        trigger=IntervalTrigger(hours=1),
        id='cleanup_pending_bookings',
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_expired_fallback_connections,
        trigger=IntervalTrigger(minutes=5),
        id='cleanup_fallback_connections',
        replace_existing=True
    )

    scheduler.start()
    current_app.logger.info("Background scheduler started")


def cleanup_expired_pending_bookings():
    """Clean up expired pending bookings"""
    try:
        with app.app_context():
            expired_count = PendingBooking.query.filter(PendingBooking.expires_at < datetime.now()).delete()
            db.session.commit()
            if expired_count > 0:
                current_app.logger.info(f"Cleaned up {expired_count} expired pending bookings")
    except Exception as e:
        current_app.logger.error(f"Failed to clean up expired pending bookings: {str(e)}")
        db.session.rollback()


def cleanup_expired_fallback_connections():
    """Clean up expired fallback connections"""
    try:
        with app.app_context():
            expired_count = ActiveConnection.query.filter(
                ActiveConnection.expires_at < datetime.now()
            ).delete()
            db.session.commit()
            if expired_count > 0:
                current_app.logger.info(f"Cleaned up {expired_count} expired fallback connections")
    except Exception as e:
        current_app.logger.error(f"Failed to clean up expired fallback connections: {str(e)}")
        db.session.rollback()

init_scheduler()