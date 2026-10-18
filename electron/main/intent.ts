export type JarvisIntent =
  | { type: "open_app"; app: string }
  | { type: "close_app"; app: string }
  | { type: "open_website"; url: string }
  | { type: "list_files"; path: string }
  | { type: "create_folder"; path: string }
  | { type: "organise_files"; path: string }
  | { type: "delete_file"; path: string }
  | { type: "run_workflow"; workflowId: string }
  | { type: "status" }
  | { type: "help" }
  | { type: "check_updates" }
  | { type: "install_update" }
  | { type: "reply"; text: string };

const appAliases: Record<string, string> = {
  notepad: "notepad",
  "text editor": "notepad",
  calculator: "calculator",
  calc: "calculator",
  explorer: "explorer",
  "file explorer": "explorer",
  files: "explorer",
  paint: "paint",
  mspaint: "paint",
  terminal: "terminal",
  "windows terminal": "terminal",
};

const websiteAliases: Record<string, string> = {
  github: "https://github.com",
  google: "https://www.google.com",
  "meta ads": "https://business.facebook.com/adsmanager",
  "ads manager": "https://business.facebook.com/adsmanager",
  shopify: "https://admin.shopify.com",
  "shopify admin": "https://admin.shopify.com",
};

export function simplifyCommand(input: string): string {
  let value = input.trim().replace(/[.!?]+$/g, "").trim();
  value = value.replace(/^(?:(?:hey|hi|okay|ok)\s+)?jarvis\s*[,;:]?\s*/i, "");
  let previous = "";
  while (previous !== value) {
    previous = value;
    value = value.replace(/^(?:please\s+|can you\s+|could you\s+|would you\s+|will you\s+|i (?:want|need) you to\s+)/i, "");
  }
  return value.replace(/\s+(?:please|for me|thanks|thank you)$/i, "").trim();
}

function cleanAppName(input: string): string {
  const clean = input.replace(/^(?:the|my)\s+/i, "").replace(/\s+(?:app|application|window)$/i, "").trim();
  return appAliases[clean.toLowerCase()] ?? clean;
}

export function parseLocalIntent(input: string): JarvisIntent | null {
  const command = simplifyCommand(input);
  let match: RegExpMatchArray | null;

  if (/^(?:status|system status|are you (?:online|working)|what are you doing)$/i.test(command)) return { type: "status" };
  if (/^(?:help|what can you do|show commands|commands)$/i.test(command)) return { type: "help" };
  if (/^(?:check|look)(?: for)? updates?$|^check if (?:you|jarvis) (?:need|needs) (?:an )?update$/i.test(command)) return { type: "check_updates" };
  if (/^(?:install|apply|do)(?: the)? update$/i.test(command)) return { type: "install_update" };

  if ((match = command.match(/^(?:close|quit|exit|stop|shut down)\s+(.+)$/i))) {
    const target = cleanAppName(match[1]);
    if (/^(?:browser|web browser|controlled browser)$/i.test(target)) return { type: "close_app", app: "browser" };
    return { type: "close_app", app: target };
  }

  if ((match = command.match(/^(?:open|launch|start)\s+(?:up\s+)?(.+)$/i))) {
    const target = cleanAppName(match[1]);
    const alias = websiteAliases[target.toLowerCase()];
    if (alias) return { type: "open_website", url: alias };
    if (/^(?:https?:\/\/)?(?:[\w-]+\.)+[a-z]{2,}(?:\/\S*)?$/i.test(target)) return { type: "open_website", url: target };
    return { type: "open_app", app: target };
  }

  if ((match = command.match(/^(?:list|show(?: me)?|display)\s+(?:the\s+)?files(?: and folders)?(?: inside| in| from)?\s+(.+)$/i))) {
    return { type: "list_files", path: match[1].trim() };
  }
  if ((match = command.match(/^what files (?:are )?in\s+(.+)$/i))) return { type: "list_files", path: match[1].trim() };
  if ((match = command.match(/^(?:create|make|add)\s+(?:a\s+)?(?:new\s+)?folder(?: called| named)?\s+(.+)$/i))) {
    return { type: "create_folder", path: match[1].trim() };
  }
  if ((match = command.match(/^(?:organise|organize|sort|tidy)(?: the)? files(?: inside| in| from)?\s+(.+)$/i))) {
    return { type: "organise_files", path: match[1].trim() };
  }
  if ((match = command.match(/^(?:delete|remove)(?: the)? file\s+(.+)$/i)) || (match = command.match(/^delete\s+(.+)$/i))) {
    return { type: "delete_file", path: match[1].trim() };
  }
  if ((match = command.match(/^(?:run|start)\s+(?:the\s+)?(?:workflow\s+)?([\w-]+)(?:\s+workflow)?$/i))) {
    return { type: "run_workflow", workflowId: match[1].toLowerCase() };
  }
  return null;
}

