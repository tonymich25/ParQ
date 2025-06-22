from flask import Blueprint, render_template, flash, redirect, url_for, session, request
from flask_login import login_user, logout_user, login_required, current_user

from argon2 import PasswordHasher
from config import User, db


accounts_bp = Blueprint('accounts', __name__, template_folder='templates')

passwordHasher = PasswordHasher()

@accounts_bp.route('/register', methods=['GET', 'POST'])
def registration():


    # Create Unauthorised Role Access Attempt log when user is authenticated and is trying to access /registration
    if current_user.is_authenticated:
        # Change to log_security event method
        logger.warning('[User:{}, Role:{}, URL requested:{}, IP:{}] Unauthorised Role Access Attempt'.format(current_user.email,
                                                                                                  current_user.role,
                                                                                                  request.url,
                                                                                                  request.remote_addr))
        # Flash error message
        flash('You are already logged in.', 'info')
        return redirect(url_for('posts.posts'))


    form = RegistrationForm()

    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already exists', category="danger")
            return redirect(url_for('accounts.registration'))


        password = form.password.data
        password_hash = passwordHasher.hash(password)
        new_user = User(
            email=form.email.data,
            firstname=form.firstname.data,
            lastname=form.lastname.data,
            phone=form.phone.data,
            password=password_hash
        )

        db.session.add(new_user)
        db.session.commit()

        log_security_event(new_user, "REGISTER", "Successful Registration", "INFO")

        return redirect(url_for('accounts.login'))

    return render_template('accounts/register.html', form=form)


@accounts_bp.route('/login', methods=['GET', 'POST'])
def login():

    # Create Unauthorised Role Access Attempt log when user is authenticated and is trying to access /login
    if current_user.is_authenticated:

        # Change to log_security event method
        logger.warning('[User:{}, Role:{}, URL requested:{}, IP:{}] Unauthorised Role Access Attempt'.format(current_user.email,
                                                                                                  current_user.role,
                                                                                                  request.url,
                                                                                                  request.remote_addr))
        # Flash error message
        flash('You are already logged in.', 'info')
        return redirect(url_for('posts.posts'))


    form = LoginForm()


    if 'attempts' not in session:
        session['attempts'] = 0

    max_attempts = 3

    if session['attempts'] >= max_attempts:
        flash('Your account has been locked due to too many failed login attempts. ' +
              'Click the link below to unlock.',
              category='danger')
        return render_template('accounts/locked.html')


    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()

        if user and user.check_password(form.password.data):
            session['attempts'] = 0  # Reset attempts on successful login
            login_user(user)
            # Editing the user log for login dates and IP addresses.
            user.log.previouslogin = user.log.latestlogin
            user.log.previousIP = user.log.latestIP
            user.log.latestlogin = datetime.now()
            user.log.latestIP = request.remote_addr
            # Making the user's active field equal to true as they have now logged in
            user.active = True
            # Saving the log changes.
            db.session.commit()
            # If the user has only logged in once then they won't have a
            # previous login to compare with.
            if user.log.previouslogin is None:
                user.current_streak = 0
            # Adding 1 to the streak if today is one more day than the last login
            elif user.log.latestlogin.date() == user.log.previouslogin.date() + timedelta(days=1):
                user.current_streak += 1
            # This would mean that the user has missed a day so the streak is reset
            elif user.log.latestlogin != user.log.previouslogin:
                user.current_streak = 0
            # Saving the streak changes.
            db.session.commit()

            log_security_event(current_user, "LOGIN",
                               "Successful Login", "INFO")

            flash('Login successful!', category='success')
            return redirect(url_for('accounts.account'))

        session['attempts'] += 1  # Increment attempts on failed login

        flash(
            f'Invalid email or password. Attempt {session["attempts"]} of {max_attempts}.',
            category='danger')

        if session['attempts'] >= max_attempts:
            flash('Your account has been locked due to too many failed login attempts.',
                  category='danger')
            return render_template('accounts/locked.html')

    return render_template('accounts/login.html', form=form)


@accounts_bp.route('/logout')
def logout():
    """
    This function is the route for logging out the current user.

    returns A redirect to the login page upon successful logout.
    """
    if current_user.is_authenticated:
        log_security_event(current_user, "LOGOUT",
                           "Successful Logout", "INFO")
        logout_user()
        flash('You have been logged out.', 'success')
        return redirect(url_for('accounts.login'))
    flash('You are not logged in', category="danger")
    return redirect(url_for('accounts.login'))