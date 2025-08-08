# ⚠ License Notice
### This project is provided for viewing as part of my personal portfolio. 
### Use, reproduction, modification, or distribution of any part of this code without 
### explicit written permission is prohibited.


# How to run application
1. Use python 3.9 and download all packages form requirements.txt
2. Run app.py
3. Connect to 127.0.0.1:5000 on a web browser and the page should load
4. Login with pre-existing user credentials:
Email: mail@mail.com
Password: A!bcd1234
5. When going to pay for a booking and stripe asks for a credit card, use stripe's special testing card credentials:
Card Number: 4242 4242 4242 4242 4242
Exp: 12/23
CVV: 123

If pre-existing user does not work, either create a new account or restart fresh with a new db. If you would like to continue with the second option then:
1. Delete folders and files: Instance + Migrations
2. Write `flask db init` -> `flask db migrate` -> `flask db upgrade`
3. New db successfully initialised and now register as a new user to get access to the dashboard and booking page

# Inspiration
The core idea behind ParQ was to solve a real-world daily problem that hundreds of thousands of people face in Cyprus, and in many other countries around the globe: parking. I have personally experienced the struggle of trying to find a parking spot no matter the day and hour of the week, leading me to waste time and sometimes not even successfully park. Having to take coins out to pay for my spot and the inaccurate information about available parking spots are also contributing factors to this overdue problem. I decided to take matters into my own hands and solve a problem using modern technology and AI, not only for my benefit but for the wider community.
# What ParQ does
ParQ allows users to book the exact parking spot they want in advance, receive AI-powered insights based on their chosen location, date, and time, get real-time live updates of parking availability, and manage their upcoming bookings through a sleek, user-friendly web app. After booking, users receive a unique QR code that acts as their digital parking pass.
# How I built it
With some prior research and experience in web app development, I carefully selected the following technologies for this project.

## Front End
* JavaScript 
* Flask Templates (Jinja2) 
* Bootstrap 5 
* Custom CSS
* Mapbox GL JS for interactive map views

## Back End
* Python with Flask
* SQLAlchemy for database handling
* WTForms for form validation
* Stripe API for payments
* Cryptography for secure data
* Pillow 
* QR Code Generation

## Database
* SQLite during development (planned PostgreSQL for production)

# Challenges I ran into
The biggest challenge was managing the limited time available to build such a complex system from scratch. The intricate logic behind the booking management system took longer to develop than I initially anticipated, which impacted the project timeline. Despite setbacks, steady progress was maintained through perseverance and willingness to put in effort. Additionally, another major complexity was UI flow and design. Delivering a user-friendly, smart, smooth and satisfying user experience was one of my key goals, requiring careful attention to detail and consuming more of my limited time. Despite these hurdles, the extra effort paid off, and I have delivered an enjoyable experience for users.
# Accomplishments I am proud of
My most significant accomplishment for this event was that I provided a smart, scalable and technologically advanced solution to a daily problem that I and many others face. I have also exercised my creativity, delivering a product that uses cutting-edge technology, with a modern and polished interface. I am also proud that I pushed myself to enter a competition, compete against other like-minded individuals and witness the value of dedicated effort.
# What's next for ParQ
The next chapter for ParQ will be focusing on turning it fully deployable as a real-world solution. I plan to integrate the system with smart parking barriers and sensors to allow automatic access via users' unique QR code or number plate. In addition, I will migrate from SQLite to a scalable cloud database and implement push notifications for reminders and live updates. In the longer term, I would like to partner with municipalities and private lot owners to make parking once and for all simple for everyone.