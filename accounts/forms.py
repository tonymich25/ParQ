from flask_wtf import FlaskForm, RecaptchaField
from wtforms import StringField, PasswordField, SubmitField, EmailField
from wtforms.validators import DataRequired, EqualTo, Regexp, Length



class RegistrationForm(FlaskForm):


    email = EmailField(validators=[DataRequired()])
    firstname = StringField(validators=[DataRequired(), Regexp('^[a-zA-Z-]+$', message='First name must contain only letters and or hyphens!')])
    lastname = StringField(validators=[DataRequired(), Regexp('^[a-zA-Z-]+$', message='Last name must contain only letters and or hyphens!')])
    phone = StringField(validators=[DataRequired(), Regexp(r'^([2-9]\d{7})$',message='Phone number must be a valid 8-digit Cyprus number.')])
    password = PasswordField(validators=[DataRequired(),
                                         Length(min=8,max=15),
                                         Regexp('.*[A-Z]', message='Password must contain one upper case letter!'),
                                         Regexp('.*[a-z]', message='Password must contain one lower case letter!'),
                                         Regexp('.*\d', message='Password must contain one digit!'),
                                         Regexp('.*\W', message='Password must contain one special (non-word) character!')
                                         ])
    confirm_password = PasswordField(validators=[DataRequired(), EqualTo('password',
                                                                         message='Both password fields must be equal!')])
    submit = SubmitField()




class LoginForm(FlaskForm):

    email = EmailField(validators=[DataRequired()])
    password = PasswordField(validators=[DataRequired()])
    recaptcha = RecaptchaField()
    submit = SubmitField()