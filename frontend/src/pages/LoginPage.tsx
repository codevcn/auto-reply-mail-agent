import React, { useState } from "react";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Banner } from "../components/ui/Banner";

export interface LoginPageProps {
  onLoginSuccess: (user: any) => void;
}

export const LoginPage: React.FC<LoginPageProps> = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    if (!username.trim() || !password) {
      setErrorMessage("Please enter both username and password.");
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: username.trim(),
          password,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        const errorMsg = data.message || data.detail || "Authentication failed. Please verify credentials.";
        setErrorMessage(errorMsg);
        setIsLoading(false);
        return;
      }

      onLoginSuccess(data);
    } catch (err: any) {
      setErrorMessage(err.message || "Network error. Please try again later.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--ds-background-subtle)] flex items-center justify-center p-4">
      <div className="w-full max-w-sm bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg p-6 shadow-md space-y-6">
        <div className="text-center space-y-1">
          <div className="inline-flex w-10 h-10 rounded bg-[var(--ds-background-brand-bold)] items-center justify-center text-[var(--ds-text-inverse)] font-bold text-base mb-2">
            MA
          </div>
          <h1 className="text-xl font-bold text-[var(--ds-text)]">Mail Agent</h1>
          <p className="text-xs text-[var(--ds-text-subtle)]">
            Shopify Support Automation & Reply Hub
          </p>
        </div>

        {errorMessage && (
          <Banner
            type="danger"
            data-testid="login-error-banner"
            title="Authentication Error"
          >
            {errorMessage}
          </Banner>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            data-testid="login-username-input"
            label="Username"
            placeholder="admin"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={isLoading}
            required
          />

          <Input
            data-testid="login-password-input"
            type="password"
            label="Password"
            placeholder="••••••••"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={isLoading}
            required
          />

          <Button
            data-testid="login-submit-button"
            type="submit"
            variant="primary"
            isLoading={isLoading}
            className="w-full mt-2"
          >
            Sign in
          </Button>
        </form>

        <div className="text-center">
          <p className="text-[11px] text-[var(--ds-text-subtle)]">
            Internal secure system. Argon2id protected sessions.
          </p>
        </div>
      </div>
    </div>
  );
};
