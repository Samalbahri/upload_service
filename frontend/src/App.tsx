
import { useEffect, useState } from "react";

type ApiResponse = {
  message: string;
  status: string;
};

function App() {
  const [message, setMessage] = useState("Loading...");

  useEffect(() => {
    async function fetchMessage() {
      try {
        const response = await fetch(
          "http://localhost:8000/api/hello"
        );

        if (!response.ok) {
          throw new Error("API request failed");
        }

        const data: ApiResponse = await response.json();
        setMessage(data.message);
      } catch (error) {
        console.error(error);
        setMessage("Cannot connect to backend");
      }
    }

    fetchMessage();
  }, []);

  return (
    <main style={{ padding: "40px" }}>
      <h1>My Full Stack Application</h1>
      <p>{message}</p>
    </main>
  );
}

export default App;
