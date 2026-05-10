# Damm Motion Platform

Damm Motion is a logistics, fleet management, and delivery tracking platform built for Interhack 2026. It provides drivers and administrators with real-time route execution tools, 3D truck interior visualization, and hands-free voice assistance.

## Features

- **Role-Based Access**: Dedicated interfaces for Drivers (route execution) and Admins (fleet overview).
- **Interactive Maps**: Real-time route tracking, delivery status mapping, and live GPS positioning using Google Maps.
- **Voice Assistant**: Integrated hands-free voice commands using Web Speech API and ElevenLabs for Text-to-Speech (TTS), allowing drivers to mark deliveries or navigate without touching the screen.
- **3D Truck Visualization**: View the truck's cargo interior, pallet loads, and free slots in 3D using React Three Fiber.
- **Real-Time Data**: Powered by Firebase Auth and Firestore for live synchronization of fleet locations and delivery states.

## Tech Stack

- **Frontend**: React, Vite
- **3D Rendering**: Three.js, React Three Fiber (R3F), Drei
- **Maps & Routing**: Google Maps API
- **Backend & Database**: Firebase (Authentication, Firestore)
- **Voice/TTS**: ElevenLabs API, Web Speech API

## Getting Started

### Prerequisites

- Node.js (v18+)
- Firebase project configured
- Google Cloud Project with Maps JavaScript API enabled

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/interhack-2026.git
   cd interhack-2026
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Setup environment variables:
   Create a `.env` file in the root directory (use `.env.example` as a template) and add your Firebase and Google Maps API keys.

4. Start the development server:
   ```bash
   npm run dev
   ```