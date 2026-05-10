import { initializeApp } from "firebase/app";
import {
  getFirestore,
  collection,
  doc,
  setDoc,
  updateDoc,
  getDocFromServer,
  getDocs,
  onSnapshot,
} from "firebase/firestore";
import {
  getAuth,
  signInWithEmailAndPassword,
  signOut,
  createUserWithEmailAndPassword,
} from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
export const auth = getAuth(app);

// Driver emails are constructed as {driverId}@interhack.bcn — never exposed to users
const toEmail = (driverId) => `${driverId}@interhack.bcn`;
export const getDriverId = (user) => user.email.replace("@interhack.bcn", "");

export async function loginDriver(driverId, password) {
  const { user } = await signInWithEmailAndPassword(auth, toEmail(driverId), password);
  return user;
}

export async function logoutDriver() {
  await signOut(auth);
}

// Called by the admin/backend when creating a new driver account
export async function createDriverAccount(driverId, password) {
  const { user } = await createUserWithEmailAndPassword(auth, toEmail(driverId), password);
  return user;
}

// Firestore schema:
// routes/{driverId}
//   driver_id:       string
//   truck_id:        string
//   points:          Array<{ lat, lng, address? }>         ordered
//   windows:         Array<{ start: string, end: string }> ordered
//   service_times:   Array<number>                         minutes per stop
//   delivery_status: Array<"pending"|"delivered">          ordered
//   status:          "pending" | "active" | "completed"

export async function setRoute(driverId, { truckId, points, windows, serviceTimes, deliveryStatus }) {
  await setDoc(doc(db, "routes", driverId), {
    driver_id: driverId,
    truck_id: truckId,
    points,
    windows,
    service_times: serviceTimes,
    delivery_status: deliveryStatus ?? new Array(points.length).fill("pending"),
    status: "pending",
  });
}

export async function markStopDelivered(driverId, statusArray) {
  await updateDoc(doc(db, "routes", driverId), { delivery_status: statusArray });
}

export function subscribeToRoute(driverId, onChange, onError) {
  return onSnapshot(
    doc(db, "routes", driverId),
    (snap) => onChange(snap.exists() ? snap.data() : null),
    onError ?? ((err) => console.error("subscribeToRoute:", err))
  );
}

export async function getRoute(driverId) {
  const snap = await getDocFromServer(doc(db, "routes", driverId));
  return snap.exists() ? snap.data() : null;
}

export async function getAllRoutes() {
  const snap = await getDocs(collection(db, "routes"));
  return snap.docs.map((d) => d.data());
}

// Real-time listener — calls onChange(routes[]) whenever Firestore updates
export function subscribeToRoutes(onChange, onError) {
  return onSnapshot(
    collection(db, "routes"),
    (snap) => onChange(snap.docs.map((d) => d.data())),
    onError ?? ((err) => console.error("subscribeToRoutes:", err))
  );
}
