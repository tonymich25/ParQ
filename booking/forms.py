from flask_wtf import FlaskForm
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired


class BookingForm(FlaskForm):

    city = SelectField("City", choices=[], validators=[DataRequired()])
    parkingLot = SelectField("Parking Lot", choices=[], validators=[DataRequired()])
    submit = SubmitField()