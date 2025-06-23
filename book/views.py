from flask import Blueprint, render_template

from book.forms import BookingForm
from config import City, db

book_bp = Blueprint('book', __name__, template_folder='templates')


@book_bp.route('/book', methods=['GET', 'POST'])
def book():

        form = BookingForm()


        cities = City.query.all()

        form.city.choices = [(city.id, city.city) for city in cities]

        return render_template('book/book.html', form=form)


