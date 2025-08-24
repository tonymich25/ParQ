import os
import stripe
from dotenv import load_dotenv
from flask import Flask, url_for, render_template
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask_admin.menu import MenuLink
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, current_user
from flask_migrate import Migrate
from sqlalchemy import MetaData

app = Flask(__name__)

# LOAD .ENV FILE
load_dotenv()

# SECRET KEY FOR FLASK FORMS
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['RECAPTCHA_PRIVATE_KEY'] = os.getenv('RECAPTCHA_PRIVATE_KEY')
app.config['RECAPTCHA_PUBLIC_KEY'] = os.getenv('RECAPTCHA_PUBLIC_KEY')

# Initialising Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'accounts.login'
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


# STRIPE Init
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
stripe.api_key = STRIPE_SECRET_KEY

socketio = SocketIO(app, cors_allowed_origins=["https://parqlive.com", "https://www.parqlive.com"], async_mode='eventlet')

# DATABASE CONFIGURATION
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_ECHO'] = True if os.getenv('SQLALCHEMY_ECHO') == 'True' else False
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = True if os.getenv('SQLALCHEMY_TRACK_MODIFICATIONS') == 'True' else False

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_names)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s"
    }
)

db = SQLAlchemy(app, metadata=metadata)
migrate = Migrate(app, db)


@app.route('/health')
def health():
    return "OK", 200

@app.route('/')
def index():
    return render_template('index.html')

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(100), nullable=False)
    firstname = db.Column(db.String(100), nullable=False)
    lastname = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(100), nullable=False)
    log = db.relationship("Log", uselist=False, back_populates="user")
    bookings = db.relationship('Booking', backref='user', lazy=True)

    def __init__(self, email, firstname, lastname, phone, password):
        self.email = email
        self.firstname = firstname
        self.lastname = lastname
        self.phone = phone
        self.password = password
        self.role = "end_user"

    @login_manager.user_loader
    def load_user(id):
        return User.query.get(int(id))

    def get_id(self):
        return int(self.id)

    def check_password(self, password):
        try:
            correct_password = passwordHasher.verify(self.password, password)
        except:
            correct_password = False
        return correct_password

    def generate_log(self):
        user_log = Log(self.id)
        self.log = user_log
        db.session.commit()


class Log(db.Model):
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    registration = db.Column(db.DateTime, nullable=False)
    latestlogin = db.Column(db.DateTime, nullable=True)
    previouslogin = db.Column(db.DateTime, nullable=True)
    latestIP = db.Column(db.String(100), nullable=True)
    previousIP = db.Column(db.String(100), nullable=True)
    user = db.relationship("User", back_populates="log")

    def __init__(self, userid):
        self.userid = userid
        self.registration = datetime.now()


class Booking(db.Model, UserMixin):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    parking_lot_id = db.Column(db.Integer, db.ForeignKey('parking_lots.id'), nullable=False)
    spot_id = db.Column(db.Integer, db.ForeignKey('parking_spots.id'), nullable=False)
    timeBooked = db.Column(db.DateTime, default=datetime.now, nullable=False)
    bookingDate = db.Column(db.Date, nullable=False)
    startTime = db.Column(db.Time, nullable=False)
    endTime = db.Column(db.Time, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    parking_spot = db.relationship('ParkingSpot', back_populates='bookings')
    parking_lot = db.relationship('ParkingLot')  # New relationship


    def __init__(self, userid, parking_lot_id, spot_id, bookingDate, startTime, endTime, amount):
        self.userid = userid
        self.parking_lot_id = parking_lot_id
        self.spot_id = spot_id
        self.bookingDate = bookingDate
        self.startTime = startTime
        self.endTime = endTime
        self.amount = amount


class City(db.Model, UserMixin):
    __tablename__ = 'cities'
    id = db.Column(db.Integer, primary_key=True)
    city = db.Column(db.Text, nullable=False)


class ParkingLot(db.Model, UserMixin):
    __tablename__ = 'parking_lots'
    id = db.Column(db.Integer, primary_key=True)
    city_id = db.Column(db.Integer, db.ForeignKey('cities.id'))
    name = db.Column(db.String(100), nullable=False)
    lat = db.Column(db.Float(precision=53), nullable=False)
    long = db.Column(db.Float(precision=53), nullable=False)
    address = db.Column(db.String(100), nullable=False)
    image_filename = db.Column(db.String(100), nullable=True)
    spots = db.relationship('ParkingSpot', backref='parking_lot', lazy=True, cascade="all, delete-orphan")


class ParkingSpot(db.Model, UserMixin):
    __tablename__ = 'parking_spots'
    id = db.Column(db.Integer, primary_key=True)
    parkingLotId = db.Column(db.Integer, db.ForeignKey('parking_lots.id'), nullable=False)
    spotNumber = db.Column(db.String(20), nullable=False)
    svgCoords = db.Column(db.String(100), nullable=False)
    pricePerHour = db.Column(db.Float, nullable=False)
    bookings = db.relationship('Booking', back_populates='parking_spot', lazy=True)



class MainIndexLink(MenuLink):
    def get_url(self):
        return url_for('index')


class ExtendedModelView(ModelView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.role == 'db_admin'


class BookingView(ExtendedModelView):
    column_display_pk = True
    column_hide_backrefs = False
    column_list = ('id', 'userid', 'parking_lot_id', 'spot_id', 'timeBooked', 'bookingDate', 'startTime', 'endTime', 'amount', 'spot_id')
    app.config['FLASK_ADMIN_FLUID_LAYOUT'] = True if os.getenv('FLASK_ADMIN_FLUID_LAYOUT') == 'True' else False


class UserView(ExtendedModelView):
    column_display_pk = True
    column_hide_backrefs = False
    column_list = ('id', 'email', 'password', 'firstname', 'lastname', 'phone', 'role', 'bookings')
    app.config['FLASK_ADMIN_FLUID_LAYOUT'] = True if os.getenv('FLASK_ADMIN_FLUID_LAYOUT') == 'True' else False


admin = Admin(app, template_mode='bootstrap4')
admin._menu = admin._menu[1:]
admin.add_link(MainIndexLink(name='Home Page'))
admin.add_view(BookingView(Booking  , db.session))
admin.add_view(UserView(User, db.session))



from accounts.views import accounts_bp, passwordHasher
from dashboard.views import dashboard_bp
from booking.views import booking_bp

app.register_blueprint(accounts_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(booking_bp)
