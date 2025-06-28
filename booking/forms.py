from flask_admin.form import DateTimeField
from flask_wtf import FlaskForm
from wtforms import SelectField, SubmitField, DateField, StringField
from wtforms.validators import DataRequired


class BookingForm(FlaskForm):

    city = SelectField(choices=[], validators=[DataRequired()])
    parkingLot = SelectField( validate_choice=False, choices=[], validators=[DataRequired()])
    bookingDate = DateField('Booking Date', format='%Y-%m-%d',validators=[DataRequired()])
    startTime = StringField('Start Time', validators=[DataRequired()])
    endTime = StringField('End Time', validators=[DataRequired()])
    submit = SubmitField()