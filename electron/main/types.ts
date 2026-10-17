export type TaskStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
export type ActionStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type Risk = "read" | "change" | "critical";

export interface ActionEvent {
  id: string;
  taskId: string;
  label: string;
  detail?: string;
  status: ActionStatus;
  risk: Risk;
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
  kind: "delete" | "publish" | "spend" | "significant_change";
  summary: string;
  detail: string;
  createdAt: string;
  expiresAt: string;
}

export interface ActivityEvent {
  id: string;
  time: string;
  level: "info" | "success" | "warning" | "error";
  source: "brain" | "browser" | "files" | "skills" | "creative" | "meta";
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
  };
  tasks: JarvisTask[];
  approvals: Approval[];
  activity: ActivityEvent[];
}

export interface WorkflowStep {
  type: "navigate" | "checkpoint" | "publish" | "spend";
  label: string;
  url?: string;
  amountAud?: number;
}

export interface Workflow {
  id: string;
  label: string;
  description: string;
  costAud: number;
  steps: WorkflowStep[];
}
