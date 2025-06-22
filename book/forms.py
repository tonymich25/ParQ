from flask_wtf import FlaskForm
from wtforms import SelectField
from wtforms.validators import DataRequired


class BookForm(FlaskForm):

    city = SelectField("City", choices=[
        ("Limassol", "Limassol"),
        ("Nicosia", "Nicosia"),
        ("Nicosia", "Nicosia"),
        ], validators=[DataRequired()])

