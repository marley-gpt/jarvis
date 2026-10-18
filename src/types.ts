export type TaskStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";

export interface ActionEvent {
  id: string;
  label: string;
  detail?: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  risk: "read" | "change" | "critical";
  startedAt: string;
  finishedAt?: string;
  error?: string;
}

export interface JarvisTask {
  id: string;
  command: string;
  title: string;
  status: TaskStatus;
  progress: number;
  createdAt: string;
  updatedAt: string;
  result?: string;
  error?: string;
  actions: ActionEvent[];
}

export interface Approval {
  id: string;
  taskId: string;
  kind: "delete" | "publish" | "spend" | "significant_change" | "close_app" | "update";
  summary: string;
  detail: string;
  createdAt: string;
  expiresAt: string;
}

export interface ActivityEvent {
  id: string;
  time: string;
  level: "info" | "success" | "warning" | "error";
  source: "brain" | "browser" | "files" | "skills" | "creative" | "meta" | "system";
  message: string;
}

export interface Snapshot {
  system: {
    status: "idle" | "active" | "awaiting_approval" | "stopped" | "error";
    emergencyStop: boolean;
    voiceAvailable: boolean;
    allowedRoots: string[];
    allowedOrigins: string[];
    budgetAud: number;
    spentAud: number;
    brainConfigured: boolean;
    brainVerified: boolean;
    brainModel: string;
    brainSpentAud: number;
    brainMonthlyLimitAud: number;
    update: {
      checking: boolean;
      available: boolean;
      canInstall: boolean;
      currentVersion: string;
      latestVersion?: string;
      message: string;
      lastCheckedAt?: string;
    };
  };
  tasks: JarvisTask[];
  approvals: Approval[];
  activity: ActivityEvent[];
}

declare global {
  interface Window {
    jarvis: {
      getSnapshot(): Promise<Snapshot>;
      submit(command: string): Promise<{ taskId: string }>;
      respondToApproval(approvalId: string, allow: boolean): Promise<void>;
      emergencyStop(): Promise<void>;
      resetEmergencyStop(): Promise<void>;
      configureBrain(apiKey: string | null): Promise<Snapshot>;
      testBrain(): Promise<Snapshot>;
      checkForUpdates(): Promise<void>;
      onSnapshot(listener: (snapshot: Snapshot) => void): () => void;
    };
    webkitSpeechRecognition?: new () => {
      continuous: boolean;
      interimResults: boolean;
      lang: string;
      start(): void;
      stop(): void;
      onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
      onend: (() => void) | null;
      onerror: ((event: { error: string }) => void) | null;
    };
  }
}

export {};

