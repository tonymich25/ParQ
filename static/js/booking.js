// Mapbox initialization (unchanged)
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

// DOM Content Loaded (unchanged)
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

    // Get selects for start time
    const startHourSelect = document.querySelector('select[name="startHour"]');
    const startMinuteSelect = document.querySelector('select[name="startMinute"]');
    // Get selects for end time
    const endHourSelect = document.querySelector('select[name="endHour"]');
    const endMinuteSelect = document.querySelector('select[name="endMinute"]');


    function onTimeChange() {
        const startTime = startHourSelect.value + ':' + startMinuteSelect.value;
        const endTime = endHourSelect.value + ':' + endMinuteSelect.value;

        console.log("Time changed:", startTime, endTime);
        checkTimeValidity();
        openWebSocketConnection(document.getElementById('parking-lot-select').value);
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

// Your existing marker functions (unchanged)
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

// Your existing addCityMarkers function (unchanged)
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

// Your existing update functions (unchanged)
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

// Your existing checkTimeValidity (unchanged)
function checkTimeValidity() {
    const bookingDate = document.getElementById("bookingDate").value;
    const startMinute = parseInt(document.querySelector('[name="startMinute"]').value);
    const startHour = parseInt(document.querySelector('[name="startHour"]').value);
    const endMinute = parseInt(document.querySelector('[name="endMinute"]').value);
    const endHour = parseInt(document.querySelector('[name="endHour"]').value);
    const errorDiv = document.getElementById("time-error");

    let isValid = true;
    let errorMessage = "";

    if (!bookingDate) {
        isValid = false;
        errorMessage = "Please select a booking date.";
    } else if (endHour < startHour) {
        isValid = false;
        errorMessage = "End time must be after start time.";
    } else if (endHour === startHour && endMinute <= startMinute) {
        isValid = false;
        errorMessage = "End time must be after start time.";
    }

    errorDiv.textContent = errorMessage;
    document.getElementById("submit-button").disabled = !isValid;

    const parkingLotId = document.getElementById("parking-lot-select").value;
    if (parkingLotId && isValid) {
        fetchSpotStatus();
    }

    if (parkingLotId && bookingDate && startHour && endHour) {
        displayRandomAIMessage();
    }

    document.getElementById("summary-time").textContent = `${startHour.toString().padStart(2, "0")}:${startMinute.toString().padStart(2, "0")} - ${endHour.toString().padStart(2, "0")}:${endMinute.toString().padStart(2, "0")}`;
    updateStepIndicator();
    updateSpotSummary();
}

// Your existing cityChanged (unchanged)
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

// Your existing parkingLotSelected with fixed WebSocket initialization
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


// WebSocket connection - minimal fixes
let socket = null;

function openWebSocketConnection(parkingLotId) {
    const bookingDate = document.getElementById("bookingDate").value;
    const startTime = document.querySelector('[name="startHour"]').value + ":" +
                     document.querySelector('[name="startMinute"]').value;
    const endTime = document.querySelector('[name="endHour"]').value + ":" +
                   document.querySelector('[name="endMinute"]').value;

    if (!bookingDate || !parkingLotId) return;

    if (socket && socket.connected) {
        // Update subscription with current times
        socket.emit('subscribe', {
            parkingLotId: parkingLotId,
            bookingDate: bookingDate,
            startTime: startTime,
            endTime: endTime
        });
        return;
    }

    socket = io('http://127.0.0.1:5000');

    socket.on('connect', () => {
        console.log('WebSocket connected, subscribing...');
        socket.emit('subscribe', {
            parkingLotId: parkingLotId,
            bookingDate: bookingDate,
        });
    });

    socket.on('spot_update', (data) => {
        console.log('Spot update received:', data);
        updateSpotAvailability(data.spotId, data.available);
    });


    socket.on('payment_redirect', (data) => {
        window.location.href = data.url;
    });

    socket.on('booking_failed', (data) => {
        alert(`Booking failed: ${data.reason}`);
        document.getElementById("submit-button").disabled = false;
        document.getElementById("submit-button").innerHTML = 'Confirm Booking';
    });
}

function updateSpotAvailability(spotId, isAvailable) {
    console.log(`Updating spot ${spotId} to ${isAvailable ? 'available' : 'taken'}`);
    const spotElement = document.getElementById(`spot-${spotId}`);
    if (!spotElement) {
        console.log(`Spot element ${spotId} not found`);
        return;
    }

    // Remove all state classes
    spotElement.classList.remove('available', 'taken', 'selected');

    if (isAvailable) {
        spotElement.classList.add('available');
        spotElement.style.cursor = 'pointer';
        spotElement.style.pointerEvents = 'auto';

        // Completely replace the onclick handler
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

    // If this was selected spot, clear selection
    if (!isAvailable && document.getElementById("selected-spot-id").value === String(spotId)) {
        document.getElementById("selected-spot-id").value = "";
        document.getElementById("submit-button").disabled = true;
        document.getElementById("spot-summary").style.display = "none";
    }

    console.log(`Spot ${spotId} UI updated successfully`);
}

// Your existing fetchSpotStatus (unchanged)
function fetchSpotStatus() {
    const parkingLotId = document.getElementById("parking-lot-select").value;
    const bookingDate = document.getElementById("bookingDate").value;
    const startHour = document.querySelector("[name=\"startHour\"]").value.padStart(2, "0");
    const startMinute = document.querySelector('[name="startMinute"]').value.padStart(2, "0");
    const endHour = document.querySelector("[name=\"endHour\"]").value.padStart(2, "0");
    const endMinute = document.querySelector('[name="endMinute"]').value.padStart(2, "0");
    const startTime = `${startHour}:${startMinute}`;
    const endTime = `${endHour}:${endMinute}`;

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
            updateStepIndicator();
            updateSpotSummary();
        })
        .catch(error => {
            console.error("Error fetching spot status:", error);
            document.getElementById("parking-lot-status").className = "alert alert-danger";
            document.getElementById("parking-lot-status").innerHTML = '<i class="bi bi-exclamation-triangle-fill me-2"></i>Error loading spots. Please try again.';
        });
}

// Your existing renderParkingSpots (unchanged)
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
            // Use direct onclick assignment for SVG elements
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
        // Select the new spot
        document.getElementById("selected-spot-id").value = spotId;
        spotElement.classList.add("selected");
        document.getElementById("submit-button").disabled = false;
        document.getElementById("spot-summary").style.display = "block";

        // Get the price from the data attribute or fetch it
        const startHour = document.querySelector('[name="startHour"]').value;
        const startMinute = document.querySelector('[name="startMinute"]').value;
        const endHour = document.querySelector('[name="endHour"]').value;
        const endMinute = document.querySelector('[name="endMinute"]').value;

        // Calculate price (you might want to adjust this based on your pricing logic)
        const pricePerHour = 2; // Default or fetch actual price
        const hours = (parseInt(endHour) - parseInt(startHour)) +
                     (parseInt(endMinute) - parseInt(startMinute)) / 60;
        const totalPrice = (pricePerHour * hours).toFixed(2);

        document.getElementById("summary-spot").textContent = `Spot #${spotId}`;
        document.getElementById("summary-price").textContent = `€${totalPrice}`;
    } else {
        // Deselect if clicking the same spot
        document.getElementById("selected-spot-id").value = "";
        document.getElementById("submit-button").disabled = true;
        document.getElementById("spot-summary").style.display = "none";
        spotElement.classList.remove("selected");
    }

    updateStepIndicator();
    updateSpotSummary();
}


// Your existing updateSpotSummary (unchanged)
function updateSpotSummary() {
    const spotId = document.getElementById("selected-spot-id").value;
    if (spotId) {
        document.getElementById("spot-summary").style.display = "block";
    } else {
        document.getElementById("spot-summary").style.display = "none";
    }
}

// Your existing zoomToCity (unchanged)
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





// Fixed form submission handler
document.getElementById("booking-form").addEventListener("submit", function (e) {
    e.preventDefault();

    if (!socket || !socket.connected) {
        alert("Connection lost. Please refresh and try again.");
        return;
    }

    try {
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
    } catch (err) {
        console.error("Booking error:", err);
        alert("Booking failed. Please try again.");
        document.getElementById("submit-button").disabled = false;
        document.getElementById("submit-button").innerHTML = 'Confirm Booking';
    }
});

// Your existing AI messages (unchanged)
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