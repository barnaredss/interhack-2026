# Damm Motion Platform

Damm Motion is an advanced logistics, fleet management, and delivery tracking platform built for Interhack 2026. It combines highly optimized mathematical routing and packing algorithms with real-time route execution tools, 3D truck interior visualization, and hands-free voice assistance for drivers.

## Advanced Algorithmic Optimization

Our core focus in building Damm Motion was solving the heavily constrained vehicle routing and 3D bin packing problems simultaneously.

### Capacitated Vehicle Routing with Time Windows (CVRPTW)
To compute the optimal delivery sequences, we model the daily logistics as a Capacitated Vehicle Routing Problem with Time Windows using Google OR-Tools:
- **Multi-Constraint Optimization:** The solver minimizes global fleet travel time while strictly adhering to the volumetric capacity and load limits of each truck.
- **Time Window Adherence:** Each client delivery is constrained by specific opening and closing hours. The algorithm optimizes the stop sequence to respect these boundaries, strictly avoiding late deliveries.
- **Dual-Resolution Road Network:** We constructed a tailored topological road graph spanning Granollers and Mollet del Vallès using OSMnx. Inside the city bounds, the full drivable network is preserved. For inter-city travel, we filter the connecting corridor to strictly major roads, pruning search spaces exponentially.
- **True Travel Time Matrices:** Graph edges are enriched with realistic speed constraints to build exact origin-destination matrices for travel times, ensuring the routed sequences are optimized for time rather than just shortest Euclidean distance.

### Non-Ergodic Lattice Framework for 3D Cargo Packing
We implemented a custom mathematical framework to model the cargo volume and loading/unloading sequences:
- **Discrete 3D Lattice:** The truck's cargo space is mapped to a discrete lattice graph $V \subset \mathbb{Z}^3$, where each box or keg occupies specific cellular dimensions based on their physical dimensions.
- **Topological Causality:** Damm trucks utilize lateral tarps, meaning pallets are accessed from the sides (y-boundaries). We strictly enforce accessibility using a dependency poset. A loading sequence is only physically valid if every loaded unit is geometrically adjacent to a previously loaded vertex or lies on the accessible boundary.
- **Sequence Permutation & Commutation Constraints:** Our optimizer (`SmartTruckOptimizer3D`) traverses the sequence space by applying transposition operators. We mathematically derived the commutation constraints, guaranteeing that any swap during the heuristic search preserves both causal topology (no blocked access) and algebraic invariance (consistent client routing epochs).
- **Returnable Handling:** Models empty returnables (e.g. empty kegs) natively. These returns are injected into the ablation processes, ensuring that space freed by deliveries is correctly tracked and re-utilized for pickups without blocking future unloading.

## Platform Features

- **3D Truck Visualization**: Built with React Three Fiber (R3F). Drivers and Admins can view the truck's cargo interior, pallet loads, and free slots in 3D, generated directly from the lattice optimizer's output.
- **Interactive Maps**: Real-time route tracking, delivery status mapping, and live GPS positioning using Google Maps.
- **Voice Assistant**: Integrated hands-free voice commands using Web Speech API and ElevenLabs Text-to-Speech (TTS). Drivers can mark deliveries as completed, check next stops, and trigger navigation without touching the screen.
- **Role-Based Access**: Dedicated interfaces for Drivers (route execution) and Admins (fleet overview).
- **Real-Time Data**: Powered by Firebase Auth and Firestore for live synchronization of fleet locations and delivery states.

## Tech Stack

- **Optimization & Backend**: Python, OSMnx, NetworkX, Shapely, OR-Tools VRP
- **Frontend**: React, Vite
- **3D Rendering**: Three.js, React Three Fiber, Drei
- **Maps**: Google Maps API
- **Infrastructure**: Firebase (Authentication, Firestore)
- **Voice/TTS**: ElevenLabs API, Web Speech API

## Getting Started

### Prerequisites

- Node.js (v18+)
- Python 3.10+
- Firebase project configured
- Google Cloud Project with Maps JavaScript API enabled

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/barnaredss/interhack-2026.git
   cd interhack-2026
   ```

2. Install frontend dependencies:
   ```bash
   npm install
   ```

3. Setup environment variables:
   Create a `.env` file in the root directory (use `.env.example` as a template) and add your Firebase, ElevenLabs, and Google Maps API keys.

4. Start the development server:
   ```bash
   npm run dev
   ```