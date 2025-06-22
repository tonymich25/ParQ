from datetime import datetime
import os
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager
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


class User(db.Model, UserMixin):

    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)

    # User authentication information.
    email = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(100), nullable=False)

    # User information
    firstname = db.Column(db.String(100), nullable=False)
    lastname = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(100), nullable=False)

    role = db.Column(db.String(100), nullable=False)


    # Log information
    log = db.relationship("Log", uselist=False, back_populates="user")


    def __init__(self, email, firstname, lastname, phone, password):
        self.email = email
        self.firstname = firstname
        self.lastname = lastname
        self.phone = phone
        self.password = password
        self.role = "end_user"

    @login_manager.user_loader
    def load_user(id):
        """
            Returns a user from the database.

            :param id: The ID of the user to find
            :return: The ids corresponding record in the database
        """
        return User.query.get(int(id))


    def get_id(self):
        """
            Returns user's id

            :return: The user's id
        """
        return int(self.id)

    def check_password(self, password):
        """
            Checks if the given password matches the stored hashed password

            :param password: The password the user entered
            :return: Boolean value depending on if the password is correct
        """
        # An incorrect password hash will return an error
        try:
            correct_password = passwordHasher.verify(self.password, password)
        except:
            correct_password = False
        return correct_password


    # Declaring a method for generating a log
    def generate_log(self):
        """
            Creates a Log for a user
        """
        user_log = Log(self.id)
        self.log = user_log
        db.session.commit()



class Log(db.Model):
    """
        Creates a Log in the app to be added to the database.

        Attributes:
            id                  A unique identifier for a log
            userid              A foreign key to identifier what user the log belongs to
            registration        The date that the user registered their account
            latestlogin         The date of the user's last login
            previouslogin       The data of the user's last login before latestlogin
            latestIP            The last IP address that the user logged in with
            previousIP          The last IP address that the user logged in with before latestIP
    """

    __tablename__ = 'logs'

    # Declaring the fields for the log table
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    registration = db.Column(db.DateTime, nullable=False)
    latestlogin = db.Column(db.DateTime, nullable=True)
    previouslogin = db.Column(db.DateTime, nullable=True)
    latestIP = db.Column(db.String(100), nullable=True)
    previousIP = db.Column(db.String(100), nullable=True)
    user = db.relationship("User", back_populates="log")

    # Declaring the constructor
    def __init__(self, userid):
        self.userid = userid
        self.registration = datetime.now()


class Booking(db.Model, UserMixin):

    __tablename__ = 'bookings'

    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    location = db.Column(db.Text, nullable=False)
    parkingspotid = db.Column(db.Text, nullable=False)
    numplate = db.Column(db.Text, nullable=False)


from accounts.views import accounts_bp, passwordHasher
from dashboard.views import dashboard_bp
from book.views import book_bp
app.register_blueprint(accounts_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(book_bp)