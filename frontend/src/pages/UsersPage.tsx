import React, { useState, useEffect } from "react";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Banner } from "../components/ui/Banner";
import { Badge } from "../components/ui/Badge";
import { Modal } from "../components/ui/Modal";

export interface UserItem {
  id: string;
  username: string;
  is_active: boolean;
  status?: string;
  roles: string[];
  created_at?: string;
  updated_at?: string;
}

export interface UsersPageProps {
  currentUserId?: string;
}

export const UsersPage: React.FC<UsersPageProps> = ({ currentUserId }) => {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lockoutError, setLockoutError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  // Modal Create User
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Modal Reset Password
  const [resetTargetUser, setResetTargetUser] = useState<UserItem | null>(null);
  const [resetPasswordVal, setResetPasswordVal] = useState("");
  const [isResetting, setIsResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const fetchUsers = async () => {
    setIsLoading(true);
    setGeneralError(null);
    try {
      const res = await fetch("/api/users");
      if (!res.ok) {
        throw new Error("Failed to load users");
      }
      const data: UserItem[] = await res.json();
      setUsers(data);
    } catch (err: any) {
      setGeneralError(err.message || "Failed to fetch user list.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    if (!newUsername.trim() || !newPassword) {
      setCreateError("Username and password are required.");
      return;
    }

    setIsCreating(true);
    try {
      const res = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: newUsername.trim(),
          password: newPassword,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.message || data.detail || "Failed to create user.");
      }
      setIsCreateModalOpen(false);
      setNewUsername("");
      setNewPassword("");
      fetchUsers();
    } catch (err: any) {
      setCreateError(err.message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleToggleUserStatus = async (user: UserItem) => {
    setLockoutError(null);
    setGeneralError(null);

    const action = user.is_active ? "disable" : "enable";
    try {
      const res = await fetch(`/api/users/${user.id}/${action}`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        const code = data.error_code;
        const msg = data.message || data.detail || `Action ${action} failed.`;
        if (code === "SELF_DISABLE_NOT_ALLOWED" || code === "LAST_ACTIVE_USER_REQUIRED") {
          setLockoutError(msg);
        } else {
          setGeneralError(msg);
        }
        return;
      }

      fetchUsers();
    } catch (err: any) {
      setGeneralError(err.message || "Network error performing user action.");
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetTargetUser || !resetPasswordVal) return;
    setResetError(null);
    setIsResetting(true);

    try {
      const res = await fetch(`/api/users/${resetTargetUser.id}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          new_password: resetPasswordVal,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.message || data.detail || "Failed to reset password.");
      }
      setResetTargetUser(null);
      setResetPasswordVal("");
    } catch (err: any) {
      setResetError(err.message);
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--ds-text)]">User Management</h1>
          <p className="text-xs text-[var(--ds-text-subtle)] mt-0.5">
            Manage authenticated users, roles, and access credentials. Invariant R-28 &amp; R-35 protected.
          </p>
        </div>
        <Button
          data-testid="create-user-button"
          variant="primary"
          onClick={() => {
            setCreateError(null);
            setIsCreateModalOpen(true);
          }}
        >
          + Add New User
        </Button>
      </div>

      {lockoutError && (
        <Banner
          data-testid="lockout-error-banner"
          type="danger"
          title="Account Lockout Safeguard"
        >
          {lockoutError}
        </Banner>
      )}

      {generalError && (
        <Banner type="warning" title="Notice">
          {generalError}
        </Banner>
      )}

      <div className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg overflow-hidden shadow-xs">
        <table
          data-testid="user-management-table"
          className="w-full text-left border-collapse text-sm"
        >
          <thead>
            <tr className="border-b border-[var(--ds-border)] bg-[var(--ds-background-subtle)] text-[var(--ds-text-subtle)] text-xs font-semibold">
              <th className="py-2.5 px-4">Username</th>
              <th className="py-2.5 px-4">Roles</th>
              <th className="py-2.5 px-4">Status</th>
              <th className="py-2.5 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--ds-border)]">
            {isLoading && users.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-[var(--ds-text-subtle)] text-xs">
                  Loading users...
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-[var(--ds-text-subtle)] text-xs">
                  No users found.
                </td>
              </tr>
            ) : (
              users.map((u) => {
                const isCurrent = u.id === currentUserId;
                return (
                  <tr
                    key={u.id}
                    className="hover:bg-[var(--ds-background-subtle)] transition-colors"
                  >
                    <td className="py-3 px-4 font-medium text-[var(--ds-text)]">
                      <div className="flex items-center gap-2">
                        <span>{u.username}</span>
                        {isCurrent && (
                          <Badge variant="brand" className="text-[10px]">
                            You
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex gap-1 flex-wrap">
                        {u.roles?.map((r) => (
                          <Badge key={r} variant="neutral">
                            {r}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      {(u.is_active ?? (u.status === "active")) ? (
                        <Badge variant="success">Active</Badge>
                      ) : (
                        <Badge variant="danger">Disabled</Badge>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right space-x-2">
                      <Button
                        variant="subtle"
                        className="text-xs px-2 py-1"
                        onClick={() => {
                          setResetError(null);
                          setResetPasswordVal("");
                          setResetTargetUser(u);
                        }}
                      >
                        Reset Password
                      </Button>
                      <Button
                        data-testid={`disable-user-button-${u.id}`}
                        variant={u.is_active ? "danger" : "default"}
                        className="text-xs px-2.5 py-1"
                        onClick={() => handleToggleUserStatus(u)}
                      >
                        {u.is_active ? "Disable" : "Enable"}
                      </Button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Modal Create User */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        title="Create New Team User"
      >
        <form onSubmit={handleCreateUser} className="space-y-4">
          {createError && (
            <Banner type="danger" title="Error">
              {createError}
            </Banner>
          )}
          <Input
            label="Username"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            placeholder="e.g. jdoe"
            required
            disabled={isCreating}
          />
          <Input
            label="Initial Password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Min 8 characters recommended"
            required
            disabled={isCreating}
          />
          <div className="flex justify-end gap-2 pt-2 border-t border-[var(--ds-border)]">
            <Button
              type="button"
              variant="subtle"
              onClick={() => setIsCreateModalOpen(false)}
              disabled={isCreating}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              isLoading={isCreating}
            >
              Create Account
            </Button>
          </div>
        </form>
      </Modal>

      {/* Modal Reset Password */}
      <Modal
        isOpen={!!resetTargetUser}
        onClose={() => setResetTargetUser(null)}
        title={`Reset Password for ${resetTargetUser?.username || ""}`}
      >
        <form onSubmit={handleResetPassword} className="space-y-4">
          {resetError && (
            <Banner type="danger" title="Error">
              {resetError}
            </Banner>
          )}
          <p className="text-xs text-[var(--ds-text-subtle)]">
            Setting a new password will immediately revoke all active sessions for this user.
          </p>
          <Input
            label="New Password"
            type="password"
            value={resetPasswordVal}
            onChange={(e) => setResetPasswordVal(e.target.value)}
            placeholder="Enter secure new password"
            required
            disabled={isResetting}
          />
          <div className="flex justify-end gap-2 pt-2 border-t border-[var(--ds-border)]">
            <Button
              type="button"
              variant="subtle"
              onClick={() => setResetTargetUser(null)}
              disabled={isResetting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              isLoading={isResetting}
            >
              Set New Password
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
