# ⚠ License Notice
### This project is provided for viewing as part of my personal portfolio. 
### Use, reproduction, modification, or distribution of any part of this code without 
### explicit written permission is prohibited.


# ParQ – Real-Time Parking Reservation Platform  

**Production-deployed system built to address Cyprus’s parking shortage, with planned commercial rollout.**  
ParQ enables users to reserve parking in real-time, receive live updates, and pay securely, built with fault-tolerant distributed systems principles.  

---

## Features  

- Real-time parking reservations with Redis atomic locking and PostgreSQL fallback  
- Secure payments via Stripe API with idempotency keys and TTL-based expiration flows  
- Fault-tolerant communication with custom Redis Manager and health checker  
- QR code generation and validation for digital access control  
- Live parking visualization through Mapbox integration with WebSocket updates  

---

## Architecture and Tech Stack  

**Core Technologies**  
- Python (Flask), JavaScript (frontend)  
- Redis + Lua scripting, PostgreSQL  
- Socket.IO for real-time communication  
- Docker, Kubernetes (Google Kubernetes Engine)  
- NGINX with cert-manager TLS  
- Vault secrets management  

**Integrations**  
- Stripe API (secure payments)  
- Mapbox API (map rendering and visualization)  

**Reliability & Observability**  
- Structured logging with trace IDs  
- Automated cleanup of incomplete transactions with background workers  
- Health checks, liveness probes, and NGINX rate limiting  

---

## Design Decisions  

- **Distributed consistency**: Redis atomic locking with Lua scripting and PostgreSQL fallback with row-level locks and exclusion constraints  
- **Resilience**: Zero-downtime failover with emission workers and database persistence during Redis outages  
- **Security-first**: Stripe integration with idempotent flows, Argon2 password hashing, MFA, and TLS across services  
- **Deployment**: Kubernetes-managed infrastructure with automated scaling and CI/CD pipelines  

---

## Roadmap  

- Integration with smart parking barriers and license plate recognition  
- Mobile application for booking and push notifications  
- Municipal and private operator partnerships for regional rollout  
- Free parking spot maps
- Refund mechanism


---

## Background  

ParQ originated at a hackathon and evolved into a production system designed to address a critical daily infrastructure problem in Cyprus: lack of reliable parking availability and management.  
