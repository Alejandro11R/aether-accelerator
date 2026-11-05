# Privacy Policy — Aether Download Accelerator Integration

**Last Updated:** November 2026

This privacy policy applies to the **Aether Download Accelerator Integration** browser extension ("the Extension") created by Alejandro11R.

## 1. Data Collection and Usage
The Extension does **NOT** collect, sell, monitor, or transmit any personally identifiable information (PII), browsing history, or personal data to external servers or third parties.

## 2. Permissions Justification
The Extension requests the following permissions strictly for functional integration with the local Aether desktop engine:

*   **`downloads`**: Required to intercept native browser file downloads and forward the download URL to the local Aether accelerator engine (`http://127.0.0.1:8765`).
*   **`contextMenus`**: Allows users to right-click on web links and media files to trigger accelerated downloads via the "Descargar con Aether" action.
*   **`storage`**: Used exclusively on the user's local machine to persist user preferences (e.g., active thread count, automatic interception toggle).
*   **`Host Permissions (<all_urls>)`**: Necessary for the content script to detect HTML5 `<video>` and `<source>` streaming media elements across websites to display the optional floating download button.

## 3. Data Transmission
All communication occurs strictly locally between the extension and the local loopback interface (`http://127.0.0.1:8765` or `http://localhost`). No data leaves the user's local device.

## 4. Contact
For questions regarding this privacy policy or the open-source codebase, visit:  
https://github.com/Alejandro11R/aether-accelerator
