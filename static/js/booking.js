mapboxgl.accessToken = "pk.eyJ1IjoiYWJjZDEyMzQtLSIsImEiOiJjbWNiMGVkdGswODMyMmpzYWVxeXd0OHF2In0.U95wJcVAPpjsoQiRvnXe4Q";
const map = new mapboxgl.Map({
    container: "map",
    style: "mapbox://styles/mapbox/streets-v11",
    center: [33.4299, 35.1264],
    zoom: 7
});

map.addControl(new mapboxgl.NavigationControl());
map.addControl(new mapboxgl.GeolocateControl({
    positionOptions: {
        enableHighAccuracy: true
    },
}));

// Add these loading functions
function showLoadingSpinner() {
    const submitBtn = document.getElementById("submit-button");
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Processing...';
    isSubmitting = true;
}

function hideLoadingSpinner() {
    const submitBtn = document.getElementById("submit-button");
    submitBtn.disabled = false;
    submitBtn.innerHTML = 'Confirm Booking';
    isSubmitting = false;
}



document.addEventListener("DOMContentLoaded", function () {
    flatpickr("#bookingDate", {
        minDate: "today",
        dateFormat: "Y-m-d",
        onChange: function (selectedDates, dateStr, instance) {
            document.getElementById("summary-date").textContent = dateStr;
            checkTimeValidity();
            console.log('Date picked')
            openWebSocketConnection(document.getElementById('parking-lot-select').value);
            updateStepIndicator();
            updateSpotSummary();
        }
    });

    const startHourSelect = document.querySelector('select[name="startHour"]');
    const startMinuteSelect = document.querySelector('select[name="startMinute"]');
    const endHourSelect = document.querySelector('select[name="endHour"]');
    const endMinuteSelect = document.querySelector('select[name="endMinute"]');


    function onTimeChange() {
        const startTime = startHourSelect.value + ':' + startMinuteSelect.value;
        const endTime = endHourSelect.value + ':' + endMinuteSelect.value;

        console.log("Time changed:", startTime, endTime);
        checkTimeValidity();

        // 🎯 FIX: Only open WebSocket connection if we have all required data
        const parkingLotId = document.getElementById('parking-lot-select').value;
        const bookingDate = document.getElementById('bookingDate').value;

        if (parkingLotId && bookingDate) {
            openWebSocketConnection(parkingLotId);
        }

        updateStepIndicator();
        updateSpotSummary();

        document.getElementById('summary-time').textContent = startTime + ' - ' + endTime;
    }

    startHourSelect.addEventListener('change', onTimeChange);
    startMinuteSelect.addEventListener('change', onTimeChange);
    endHourSelect.addEventListener('change', onTimeChange);
    endMinuteSelect.addEventListener('change', onTimeChange);


    map.on("load", function () {
        addCityMarkers();
    });
});

let cityMarkers = [];
let parkingLotMarkers = [];

function clearParkingLotMarkers() {
    parkingLotMarkers.forEach(marker => marker.remove());
    parkingLotMarkers = [];
}

function clearCityMarkers() {
    cityMarkers.forEach(marker => marker.remove());
    cityMarkers = [];
}

function addCityMarkers() {
    clearCityMarkers();
    const cityOptions = document.getElementById("city-select").options;
    for (let i = 0; i < cityOptions.length; i++) {
        const cityName = cityOptions[i].text;
        if (!cityName || cityName === "Select a city") continue;
        fetch(`https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(`${cityName}, Cyprus`)}.json?access_token=${mapboxgl.accessToken}`)
            .then(response => response.json())
            .then(data => {
                if (data.features && data.features.length > 0) {
                    const marker = new mapboxgl.Marker({color: "#d11a2a"})
                        .setLngLat(data.features[0].center)
                        .setPopup(new mapboxgl.Popup().setHTML(`<h6>${cityName}</h6>`))
                        .addTo(map);
                    cityMarkers.push(marker);
                }
            });
    }
}

function updateStepIndicator() {
    const steps = document.querySelectorAll(".step");
    const citySelected = document.getElementById("city-select").value !== "";
    const dateSelected = document.getElementById("bookingDate").value !== "";
    const spotSelected = document.getElementById("selected-spot-id").value !== "";

    steps.forEach((step, index) => {
        step.classList.remove("active", "completed");
        if (index === 0 && citySelected) {
            step.classList.add("completed");
            steps[1].classList.add("active");
        }
        if (index === 1 && dateSelected) {
            step.classList.add("completed");
            steps[2].classList.add("active");
        }
        if (index === 2 && spotSelected) {
            step.classList.add("completed");
            steps[3].classList.add("active");
        }
    });
}

function checkTimeValidity() {
    const bookingDate = document.getElementById("bookingDate").value;
    const startMinute = parseInt(document.querySelector('[name="startMinute"]').value);
    const startHour = parseInt(document.querySelector('[name="startHour"]').value);
    const endMinute = parseInt(document.querySelector('[name="endMinute"]').value);
    const endHour = parseInt(document.querySelector('[name="endHour"]').value);
    const errorDiv = document.getElementById("time-error");
    const submitButton = document.getElementById("submit-button");

    let isValid = true;
    let errorMessage = "";

    if (!bookingDate) {
        isValid = false;
        errorMessage = "Please select a booking date.";
    } else {
        // Convert to Date objects for proper comparison
        const startTime = new Date(2000, 0, 1, startHour, startMinute);
        const endTime = new Date(2000, 0, 1, endHour, endMinute);

        if (endTime <= startTime) {
            isValid = false;
            errorMessage = "End time must be after start time.";
        }
    }

    errorDiv.textContent = errorMessage;
    submitButton.disabled = !isValid;

    const spotSelected = document.getElementById("selected-spot-id").value !== "";
    submitButton.disabled = !isValid || !spotSelected;

    const parkingLotId = document.getElementById("parking-lot-select").value;
    if (parkingLotId && isValid) {
        fetchSpotStatus();
    }

    if (parkingLotId && bookingDate && startHour !== undefined && endHour !== undefined) {
        displayRandomAIMessage();
    }

    document.getElementById("summary-time").textContent = `${startHour.toString().padStart(2, "0")}:${startMinute.toString().padStart(2, "0")} - ${endHour.toString().padStart(2, "0")}:${endMinute.toString().padStart(2, "0")}`;
    updateStepIndicator();
    updateSpotSummary();
}

function cityChanged(selectElement) {
    clearParkingLotMarkers();
    clearCityMarkers();
    const cityId = selectElement.value;
    const cityName = selectElement.options[selectElement.selectedIndex].text;
    const parkingLotSelect = document.getElementById("parking-lot-select");
    parkingLotSelect.innerHTML = '<option value="" disabled selected>Select a parking lot</option>';
    parkingLotSelect.disabled = true;
    document.getElementById("parking-lot-container").style.display = "none";
    document.getElementById("spot-rects-group").innerHTML = "";
    document.getElementById("selected-spot-id").value = "";
    document.getElementById("submit-button").disabled = true;
    cityMarkers.forEach(marker => marker.remove());
    cityMarkers = [];
    zoomToCity(cityName);
    updateStepIndicator();
    updateSpotSummary();

    fetch("/city_selected", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({city: cityId})
    })
        .then(response => response.json())
        .then(data => {
            parkingLotSelect.innerHTML = '<option value="" disabled selected>Select a parking lot</option>';
            data.forEach(lot => {
                const option = document.createElement("option");
                option.value = lot.id;
                option.textContent = lot.name;
                option.dataset.lat = lot.lat;
                option.dataset.long = lot.long;
                parkingLotSelect.appendChild(option);
                const marker = new mapboxgl.Marker({color: "#4361ee"})
                    .setLngLat([lot.lat, lot.long])
                    .setPopup(new mapboxgl.Popup().setHTML(`<h6>${lot.name}</h6><p>${lot.address || "No address"}</p>`))
                    .addTo(map);
                parkingLotMarkers.push(marker);
            });
            parkingLotSelect.disabled = false;
        });
}

function parkingLotSelected() {
    const select = document.getElementById("parking-lot-select");
    const selectedOption = select.options[select.selectedIndex];
    const parkingLotLat = parseFloat(selectedOption.dataset.lat);
    const parkingLotLong = parseFloat(selectedOption.dataset.long);
    const parkingLotName = selectedOption.text;
    const parkingLotId = document.getElementById('parking-lot-select').value;

    document.getElementById("parking-lot-container").style.display = "none";
    document.getElementById("selected-spot-id").value = "";
    document.getElementById("submit-button").disabled = true;
    document.getElementById("summary-location").textContent = parkingLotName;

    if (!isNaN(parkingLotLong) && !isNaN(parkingLotLat)) {
        map.flyTo({
            center: [parkingLotLat, parkingLotLong],
            zoom: 17
        });
        clearParkingLotMarkers();
        const marker = new mapboxgl.Marker({color: "#4361ee"})
            .setLngLat([parkingLotLat, parkingLotLong])
            .setPopup(new mapboxgl.Popup().setHTML(`<h6>${parkingLotName}</h6>`))
            .addTo(map);
        parkingLotMarkers.push(marker);
    }

    change = true;
    openWebSocketConnection(document.getElementById('parking-lot-select').value);
    checkTimeValidity();

    updateStepIndicator();
    updateSpotSummary();
}


let socket = null;

function openWebSocketConnection(parkingLotId) {
    const bookingDate = document.getElementById("bookingDate").value;
    const startHour = document.querySelector('[name="startHour"]').value;
    const startMinute = document.querySelector('[name="startMinute"]').value;
    const endHour = document.querySelector('[name="endHour"]').value;
    const endMinute = document.querySelector('[name="endMinute"]').value;

    // Ensure we have valid values (use defaults if empty)
    const startTime = startHour && startMinute ?
        `${startHour.padStart(2, '0')}:${startMinute.padStart(2, '0')}` : '00:00';
    const endTime = endHour && endMinute ?
        `${endHour.padStart(2, '0')}:${endMinute.padStart(2, '0')}` : '23:59';

    // If socket already exists and is connected, reuse it
    if (socket && socket.connected) {
        socket.emit('subscribe', {
            parkingLotId: parkingLotId,
            bookingDate: bookingDate,
            startTime: startTime,
            endTime: endTime
        });
        return;
    }

    // Only create new socket if none exists or it's disconnected
    if (!socket || !socket.connected) {
        socket = io(window.location.origin);

        // Add all event listeners here ONCE
        socket.on('connect', () => {
            console.log('WebSocket connected, subscribing...');
            socket.emit('subscribe', {
                parkingLotId: parkingLotId,
                bookingDate: bookingDate,
                startTime: startTime,
                endTime: endTime
            });
        });

        socket.on('subscription_error', (data) => {
            console.error('Subscription error:', data.message);
        });
        socket.on('spot_update', (data) => {
            console.log('Spot update received:', data);
            updateSpotAvailability(data.spotId, data.available);
        });

        socket.on('payment_redirect', (data) => {
            window.location.href = data.url;
        });


        socket.on('book_failed', (data) => {
            isBooking = false;  // Reset booking state

            if (!data.success) {
                alert(`Booking failed: ${data.reason}`);
                document.getElementById("submit-button").disabled = false;
                document.getElementById("submit-button").innerHTML = 'Confirm Booking';
            }
        });



        // Listen for direct booking success
        socket.on('booking_success', (data) => {
            console.log('✅ Direct booking success:', data);
            alert(data.message);
            // Redirect to dashboard or show confirmation
            window.location.href = '/dashboard';
        });

        // Listen for direct booking failure
        socket.on('booking_failed', (data) => {
            alert(data.reason);
        });


    }
}


function updateSpotAvailability(spotId, isAvailable) {
    console.log(`Updating spot ${spotId} to ${isAvailable ? 'available' : 'taken'}`);
    const spotElement = document.getElementById(`spot-${spotId}`);
    if (!spotElement) {
        console.log(`Spot element ${spotId} not found`);
        return;
    }

    spotElement.classList.remove('available', 'taken', 'selected');

    if (isAvailable) {
        spotElement.classList.add('available');
        spotElement.style.cursor = 'pointer';
        spotElement.style.pointerEvents = 'auto';

        spotElement.onclick = null;
        spotElement.onclick = function() {
            handleSpotClick(spotId);
        };
    } else {
        spotElement.classList.add('taken');
        spotElement.style.cursor = 'not-allowed';
        spotElement.style.pointerEvents = 'none';
        spotElement.onclick = null;
    }

    if (!isAvailable && document.getElementById("selected-spot-id").value === String(spotId)) {
        document.getElementById("selected-spot-id").value = "";
        document.getElementById("submit-button").disabled = true;
        document.getElementById("spot-summary").style.display = "none";
    }

    console.log(`Spot ${spotId} UI updated successfully`);
}

function fetchSpotStatus() {
    const parkingLotId = document.getElementById("parking-lot-select").value;
    const bookingDate = document.getElementById("bookingDate").value;
    const startHour = document.querySelector("[name=\"startHour\"]").value.padStart(2, "0");
    const startMinute = document.querySelector('[name="startMinute"]').value.padStart(2, "0");
    const endHour = document.querySelector("[name=\"endHour\"]").value.padStart(2, "0");
    const endMinute = document.querySelector('[name="endMinute"]').value.padStart(2, "0");
    const startTime = `${startHour}:${startMinute}`;
    const endTime = `${endHour}:${endMinute}`;

    console.log("Fetching spot status with:", {parkingLotId, bookingDate, startTime, endTime});

    document.getElementById("parking-lot-container").style.display = "block";
    document.getElementById("parking-lot-status").className = "alert alert-info";
    document.getElementById("parking-lot-status").innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Checking availability...';
    document.getElementById("selected-spot-id").value = "";
    document.getElementById("submit-button").disabled = true;
    document.getElementById("spot-summary").style.display = "none";

    fetch("/check_spot_availability", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            parkingLotId: parkingLotId,
            bookingDate: bookingDate,
            startTime: startTime,
            endTime: endTime,
        })
    })
        .then(response => response.json())
        .then(data => {
            renderParkingSpots(data);

            if (socket && socket.connected) {
                socket.emit('request_lease_updates', {
                    parkingLotId: parkingLotId,
                    bookingDate: bookingDate
                });
            }
        })
        .catch(error => {
            console.error("Error fetching spot status:", error);
            document.getElementById("parking-lot-status").className = "alert alert-danger";
            document.getElementById("parking-lot-status").innerHTML = '<i class="bi bi-exclamation-triangle-fill me-2"></i>Error loading spots. Please try again.';
        });
}

function renderParkingSpots(data) {
    const parkingImage = document.getElementById("parking-image");
    const spotRectsGroup = document.getElementById("spot-rects-group");

    parkingImage.setAttribute("href", `/static/images/${data.image_filename}`);
    spotRectsGroup.innerHTML = "";

    let availableCount = 0;
    data.spots.forEach(spot => {
        if (spot.is_available) availableCount++;

        const [x, y, width, height] = spot.svgCoords.split(",");

        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", x);
        rect.setAttribute("y", y);
        rect.setAttribute("width", width);
        rect.setAttribute("height", height);
        rect.setAttribute("rx", "5");
        rect.setAttribute("ry", "5");
        rect.setAttribute("id", `spot-${spot.id}`);
        rect.classList.add("parking-spot-rect");
        rect.classList.toggle("available", spot.is_available);
        rect.classList.toggle("taken", !spot.is_available);

        if (spot.is_available) {
            rect.onclick = function() {
                handleSpotClick(spot.id);
            };
        }
        spotRectsGroup.appendChild(rect);
    });

    const statusElement = document.getElementById("parking-lot-status");
    if (availableCount > 0) {
        statusElement.className = "alert alert-success";
        statusElement.innerHTML = `<i class="bi bi-check-circle-fill me-2"></i>${availableCount} of ${data.spots.length} spots available`;
    } else {
        statusElement.className = "alert alert-warning";
        statusElement.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-2"></i>No spots available for selected time`;
    }
}

function handleSpotClick(spotId) {
    const currentSelectedId = document.getElementById("selected-spot-id").value;
    const prevSelected = document.querySelector(".parking-spot-rect.selected");
    const spotElement = document.getElementById(`spot-${spotId}`);

    if (prevSelected && prevSelected !== spotElement) {
        prevSelected.classList.remove("selected");
    }

    if (currentSelectedId !== String(spotId)) {
        document.getElementById("selected-spot-id").value = spotId;
        spotElement.classList.add("selected");
        document.getElementById("submit-button").disabled = false;
        document.getElementById("spot-summary").style.display = "block";

        const startHour = document.querySelector('[name="startHour"]').value;
        const startMinute = document.querySelector('[name="startMinute"]').value;
        const endHour = document.querySelector('[name="endHour"]').value;
        const endMinute = document.querySelector('[name="endMinute"]').value;

        const pricePerHour = 2;
        const hours = (parseInt(endHour) - parseInt(startHour)) +
            (parseInt(endMinute) - parseInt(startMinute)) / 60;
        const totalPrice = (pricePerHour * hours).toFixed(2);

        document.getElementById("summary-spot").textContent = `Spot #${spotId}`;
        document.getElementById("summary-price").textContent = `€${totalPrice}`;
    } else {
        document.getElementById("selected-spot-id").value = "";
        document.getElementById("submit-button").disabled = true;
        document.getElementById("spot-summary").style.display = "none";
        spotElement.classList.remove("selected");
    }

    updateStepIndicator();
    updateSpotSummary();
}


function updateSpotSummary() {
    const spotId = document.getElementById("selected-spot-id").value;
    if (spotId) {
        document.getElementById("spot-summary").style.display = "block";
    } else {
        document.getElementById("spot-summary").style.display = "none";
    }
}

function zoomToCity(cityName) {
    if (!cityName) return;

    fetch(`https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(`${cityName}, Cyprus`)}.json?access_token=${mapboxgl.accessToken}`)
        .then(response => response.json())
        .then(data => {
            if (data.features && data.features.length > 0) {
                map.flyTo({
                    center: data.features[0].center,
                    zoom: 12
                });
            }
        });
}


let isSubmitting = false;

document.getElementById("booking-form").addEventListener("submit", function (e) {
    e.preventDefault();


    if (isSubmitting) {
        console.log("Already submitting, please wait...");
        return;
    }

    if (!socket || !socket.connected) {
        alert("Connection lost. Please refresh and try again.");
        return;
    }

    try {
        isSubmitting = true;
        const submitBtn = document.getElementById("submit-button");
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Booking...';

        const bookingMsg = {
            type: "book_spot",
            spotId: document.getElementById("selected-spot-id").value,
            parkingLotId: document.getElementById("parking-lot-select").value,
            bookingDate: document.getElementById("bookingDate").value,
            startHour: document.querySelector('[name="startHour"]').value,
            startMinute: document.querySelector('[name="startMinute"]').value,
            endHour: document.querySelector('[name="endHour"]').value,
            endMinute: document.querySelector('[name="endMinute"]').value
        };

        socket.emit('book_spot', bookingMsg);

        // Re-enable after 5 seconds if no response
        setTimeout(() => {
            isSubmitting = false;
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Confirm Booking';
        }, 5000);

    } catch (err) {
        console.error("Booking error:", err);
        alert("Booking failed. Please try again.");
        isSubmitting = false;
        document.getElementById("submit-button").disabled = false;
        document.getElementById("submit-button").innerHTML = 'Confirm Booking';
    }
});


const aiMessages = [
    "Most bookings for this location happen at around 9:30 AM daily",
    "By 11 AM 94% of spaces are booked in advance for this area",
    "This location is a hotspot and spaces quickly run out",
    "Off-peak hours parking reservations drop by 40% in this parking lot",
    "On weekdays, 85% of bays are booked during peak hours",
    "Weekend parking demand decreases by 30% compared to weekdays for this location",
    "Evening parking demand drops sharply after 7:00 PM specifically for this parking lot",
    "This area experiences high demand on weekdays, with occupancy over 90%",
    "Reservations for this location peak during business hours from 9:00 AM to 5:00 PM"
];

let change = false;

function displayRandomAIMessage() {
    if (change === true) {
        const randomMessage = aiMessages[Math.floor(Math.random() * aiMessages.length)];
        const messageElement = document.getElementById("ai-random-message");
        messageElement.innerHTML = `<small class="d-flex align-items-center gap-1">${randomMessage}</small>`;
        change = false;
    }
}
