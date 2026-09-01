import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/context/AuthContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Conversations from "@/pages/Conversations";
import Orders from "@/pages/Orders";
import Customers from "@/pages/Customers";
import Products from "@/pages/Products";
import MediaLibrary from "@/pages/MediaLibrary";
import AIAgent from "@/pages/AIAgent";
import WhatsApp from "@/pages/WhatsApp";
import Templates from "@/pages/Templates";
import KnowledgeBase from "@/pages/KnowledgeBase";
import Analytics from "@/pages/Analytics";
import Settings from "@/pages/Settings";
import Billing from "@/pages/Billing";

const P = (el) => <ProtectedRoute>{el}</ProtectedRoute>;

function App() {
  return (
    <div className="App">
      <AuthProvider>
        <BrowserRouter>
          <Toaster position="top-right" richColors />
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={P(<Dashboard />)} />
            <Route path="/conversations" element={P(<Conversations />)} />
            <Route path="/orders" element={P(<Orders />)} />
            <Route path="/customers" element={P(<Customers />)} />
            <Route path="/products" element={P(<Products />)} />
            <Route path="/media" element={P(<MediaLibrary />)} />
            <Route path="/ai-agent" element={P(<AIAgent />)} />
            <Route path="/whatsapp" element={P(<WhatsApp />)} />
            <Route path="/templates" element={P(<Templates />)} />
            <Route path="/knowledge" element={P(<KnowledgeBase />)} />
            <Route path="/analytics" element={P(<Analytics />)} />
            <Route path="/settings" element={P(<Settings />)} />
            <Route path="/billing" element={P(<Billing />)} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </div>
  );
}

export default App;
