export type Lang = "en" | "ta" | "hi";
export const LANGS: Lang[] = ["en", "ta", "hi"];

const KEY = "aira.lang";

export function loadLang(): Lang {
  const q = new URLSearchParams(location.search).get("lang");
  if (q && (LANGS as string[]).includes(q)) return q as Lang;
  try {
    const v = localStorage.getItem(KEY);
    if (v && (LANGS as string[]).includes(v)) return v as Lang;
  } catch {
    /* storage blocked */
  }
  return "en";
}

export function saveLang(lang: Lang): void {
  try {
    localStorage.setItem(KEY, lang);
  } catch {
    /* storage blocked */
  }
}

type Strings = {
  langName: string;
  hint: string;
  talkOn: string;
  talkOff: string;
  stopped: string;
  connecting: string;
  connected: string;
  reconnecting: string;
  offline: string;
  mockMode: string;
  micError: string;
  cameraError: string;
  lookingCamera: string;
  yes: string;
  no: string;
  confirmTitle: string;
  counted: (n: number, sku: string, expected: number | null) => string;
  typeInstead: string;
  send: string;
  buttonLabel: (talking: boolean) => string;
};

export const STRINGS: Record<Lang, Strings> = {
  en: {
    langName: "English",
    hint: "Double tap to talk. Long press to stop.",
    talkOn: "Listening",
    talkOff: "Stopped listening",
    stopped: "Stopped",
    connecting: "Connecting",
    connected: "Connected to AIRA",
    reconnecting: "Connection lost, reconnecting",
    offline: "No internet, waiting",
    mockMode: "Demo mode",
    micError: "Microphone is not available",
    cameraError: "Camera is not available",
    lookingCamera: "Looking",
    yes: "Yes",
    no: "No",
    confirmTitle: "Confirm",
    counted: (n, sku, e) => (e == null ? `Counted ${n} ${sku}` : `Counted ${n} ${sku}, expected ${e}`),
    typeInstead: "Type instead",
    send: "Send",
    buttonLabel: (t) => (t ? "AIRA is listening. Double tap to stop talking. Long press to stop AIRA." : "AIRA. Double tap to talk. Long press to stop."),
  },
  ta: {
    langName: "தமிழ்",
    hint: "பேச இருமுறை தட்டவும். நிறுத்த நீண்ட நேரம் அழுத்தவும்.",
    talkOn: "கேட்கிறேன்",
    talkOff: "கேட்பது நின்றது",
    stopped: "நிறுத்தப்பட்டது",
    connecting: "இணைக்கிறது",
    connected: "AIRA உடன் இணைந்தது",
    reconnecting: "இணைப்பு போனது, மீண்டும் இணைக்கிறது",
    offline: "இணையம் இல்லை, காத்திருக்கிறது",
    mockMode: "டெமோ முறை",
    micError: "மைக் கிடைக்கவில்லை",
    cameraError: "கேமரா கிடைக்கவில்லை",
    lookingCamera: "பார்க்கிறேன்",
    yes: "ஆம்",
    no: "இல்லை",
    confirmTitle: "உறுதிசெய்",
    counted: (n, sku, e) => (e == null ? `${sku}: ${n} எண்ணப்பட்டது` : `${sku}: ${n} எண்ணப்பட்டது, எதிர்பார்த்தது ${e}`),
    typeInstead: "தட்டச்சு செய்ய",
    send: "அனுப்பு",
    buttonLabel: (t) => (t ? "AIRA கேட்கிறது. நிறுத்த இருமுறை தட்டவும்." : "AIRA. பேச இருமுறை தட்டவும். நிறுத்த நீண்ட நேரம் அழுத்தவும்."),
  },
  hi: {
    langName: "हिन्दी",
    hint: "बोलने के लिए दो बार टैप करें। रोकने के लिए देर तक दबाएँ।",
    talkOn: "सुन रही हूँ",
    talkOff: "सुनना बंद",
    stopped: "रोका गया",
    connecting: "जुड़ रहा है",
    connected: "AIRA से जुड़ गया",
    reconnecting: "कनेक्शन टूटा, फिर से जुड़ रहा है",
    offline: "इंटरनेट नहीं है, इंतज़ार",
    mockMode: "डेमो मोड",
    micError: "माइक उपलब्ध नहीं है",
    cameraError: "कैमरा उपलब्ध नहीं है",
    lookingCamera: "देख रही हूँ",
    yes: "हाँ",
    no: "नहीं",
    confirmTitle: "पुष्टि करें",
    counted: (n, sku, e) => (e == null ? `${sku}: ${n} गिने` : `${sku}: ${n} गिने, अपेक्षित ${e}`),
    typeInstead: "टाइप करें",
    send: "भेजें",
    buttonLabel: (t) => (t ? "AIRA सुन रही है। बोलना बंद करने के लिए दो बार टैप करें।" : "AIRA. बोलने के लिए दो बार टैप करें। रोकने के लिए देर तक दबाएँ।"),
  },
};
