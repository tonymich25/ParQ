import os

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from flask_migrate import Migrate
from sqlalchemy import MetaData

app = Flask(__name__)


# DATABASE CONFIGURATION
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_ECHO'] = os.getenv('SQLALCHEMY_ECHO') == 'True'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = bool(os.getenv('SQLALCHEMY_TRACK_MODIFICATIONS'))


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


    def __init__(self, email, firstname, lastname, phone, password):
        self.email = email
        self.firstname = firstname
        self.lastname = lastname
        self.phone = phone
        self.password = password
        self.role = "end_user"





    def get_id(self):
        """
            Returns user's id

            :return: The user's id
        """
        return int(self.id)


from accounts.views import accounts_bp, passwordHasher
app.register_blueprint(accounts_bp)
