package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

type emulatorSendRequest struct {
	From     string   `json:"from"`
	To       []string `json:"to"`
	Subject  string   `json:"subject"`
	Priority int      `json:"priority"`
	Body     string   `json:"body"`
}

type emulatorReceipt struct {
	DeliveredAt *time.Time `json:"delivered_at,omitempty"`
	ReadAt      *time.Time `json:"read_at,omitempty"`
}

type emulatorMessage struct {
	ID        int64                      `json:"id"`
	From      string                     `json:"from"`
	To        []string                   `json:"to"`
	Subject   string                     `json:"subject,omitempty"`
	Priority  int                        `json:"priority,omitempty"`
	Body      string                     `json:"body"`
	CreatedAt time.Time                  `json:"created_at"`
	ReadBy    []string                   `json:"read_by,omitempty"`
	Receipts  map[string]emulatorReceipt `json:"receipts,omitempty"`
	Local     bool                       `json:"-"`
	Read      bool                       `json:"-"`
}

type emulatorApp struct {
	server   string
	pin      string
	reader   *bufio.Reader
	messages []emulatorMessage
	seen     map[int64]bool
}

func postJSON(url string, value interface{}, out interface{}) error {
	body, err := json.Marshal(value)
	if err != nil {
		return err
	}
	resp, err := http.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(respBody))
	}
	if out != nil && len(respBody) > 0 {
		return json.Unmarshal(respBody, out)
	}
	return nil
}

func getJSON(url string, out interface{}) error {
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(respBody))
	}
	return json.Unmarshal(respBody, out)
}

func runSend(server, from, to, subject string, priority int, body string) {
	var response map[string]interface{}
	err := postJSON(strings.TrimRight(server, "/")+"/send-pin", emulatorSendRequest{
		From:     from,
		To:       []string{to},
		Subject:  subject,
		Priority: priority,
		Body:     body,
	}, &response)
	if err != nil {
		log.Fatal(err)
	}
	pretty, _ := json.MarshalIndent(response, "", "  ")
	fmt.Println(string(pretty))
}

func runPoll(server, pin string, ack bool, interval time.Duration) {
	for {
		var messages []emulatorMessage
		url := fmt.Sprintf("%s/poll?pin=%s", strings.TrimRight(server, "/"), pin)
		if err := getJSON(url, &messages); err != nil {
			log.Println(err)
		} else if len(messages) == 0 {
			fmt.Printf("[%s] no new messages for %s\n", time.Now().Format("15:04:05"), pin)
		} else {
			for _, msg := range messages {
				fmt.Printf("\n[%s] PIN %s -> %s | id=%d | priority=%d | subject=%s\n%s\n", msg.CreatedAt.Format(time.RFC3339), msg.From, strings.Join(msg.To, ","), msg.ID, msg.Priority, msg.Subject, msg.Body)
				if ack {
					var response map[string]interface{}
					err := postJSON(strings.TrimRight(server, "/")+"/ack", map[string]interface{}{"pin": pin, "id": msg.ID}, &response)
					if err != nil {
						log.Println("ack:", err)
					}
				}
			}
		}
		if interval <= 0 {
			return
		}
		time.Sleep(interval)
	}
}

func newApp(server, pin string) *emulatorApp {
	return &emulatorApp{
		server: strings.TrimRight(server, "/"),
		pin:    pin,
		reader: bufio.NewReader(os.Stdin),
		seen:   map[int64]bool{},
	}
}

func (a *emulatorApp) prompt(label string) string {
	fmt.Print(label)
	value, _ := a.reader.ReadString('\n')
	return strings.TrimRight(value, "\r\n")
}

func (a *emulatorApp) promptMultiline(label string) string {
	fmt.Println(label)
	fmt.Println("Finish with a single dot on a new line.")
	var lines []string
	for {
		line, _ := a.reader.ReadString('\n')
		line = strings.TrimRight(line, "\r\n")
		if line == "." {
			break
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (a *emulatorApp) promptPriority(defaultValue int) int {
	raw := strings.TrimSpace(a.prompt("Priority 0=low, 1=normal, 2=high [" + strconv.Itoa(defaultValue) + "]: "))
	if raw == "" {
		return defaultValue
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < 0 || value > 2 {
		fmt.Println("Invalid priority, using default.")
		return defaultValue
	}
	return value
}

func (a *emulatorApp) poll() error {
	var messages []emulatorMessage
	if err := getJSON(fmt.Sprintf("%s/poll?pin=%s", a.server, a.pin), &messages); err != nil {
		return err
	}
	for _, msg := range messages {
		if !a.seen[msg.ID] {
			a.messages = append(a.messages, msg)
			a.seen[msg.ID] = true
		}
		var response map[string]interface{}
		if err := postJSON(a.server+"/ack", map[string]interface{}{"pin": a.pin, "id": msg.ID}, &response); err != nil {
			return err
		}
	}
	sort.SliceStable(a.messages, func(i, j int) bool {
		return a.messages[i].CreatedAt.Before(a.messages[j].CreatedAt)
	})
	fmt.Printf("Received new messages: %d\n", len(messages))
	return nil
}

func (a *emulatorApp) send(to, subject string, priority int, body string) error {
	var response map[string]interface{}
	if priority < 0 || priority > 2 {
		priority = 1
	}
	if err := postJSON(a.server+"/send-pin", emulatorSendRequest{From: a.pin, To: []string{to}, Subject: subject, Priority: priority, Body: body}, &response); err != nil {
		return err
	}
	id := -time.Now().UnixNano()
	if value, ok := response["id"].(float64); ok {
		id = int64(value)
	}
	msg := emulatorMessage{
		ID:        id,
		From:      a.pin,
		To:        []string{to},
		Subject:   subject,
		Priority:  priority,
		Body:      body,
		CreatedAt: time.Now(),
		Local:     true,
	}
	a.messages = append(a.messages, msg)
	fmt.Println("Sent.")
	return nil
}

func (a *emulatorApp) messageStatus(msg emulatorMessage) string {
	if !(msg.Local || msg.From == a.pin) {
		if msg.Read {
			return "read locally"
		}
		return "delivered locally"
	}
	for _, pin := range msg.To {
		receipt, ok := msg.Receipts[strings.ToUpper(pin)]
		if ok && receipt.ReadAt != nil {
			return "read"
		}
		if ok && receipt.DeliveredAt != nil {
			return "delivered"
		}
	}
	return "sent"
}

func (a *emulatorApp) refreshMessages() error {
	var messages []emulatorMessage
	if err := getJSON(a.server+"/messages", &messages); err != nil {
		return err
	}
	for _, remote := range messages {
		for i := range a.messages {
			if a.messages[i].ID == remote.ID {
				a.messages[i].Receipts = remote.Receipts
				a.messages[i].ReadBy = remote.ReadBy
			}
		}
	}
	return nil
}

func (a *emulatorApp) markReadInteractive() {
	msg := a.chooseMessage()
	if msg == nil {
		return
	}
	if msg.Local || msg.From == a.pin {
		fmt.Println("This is an outgoing message.")
		return
	}
	var response map[string]interface{}
	if err := postJSON(a.server+"/receipt", map[string]interface{}{"pin": a.pin, "id": msg.ID, "type": "read"}, &response); err != nil {
		fmt.Println("Read receipt error:", err)
		return
	}
	msg.Read = true
	fmt.Println("Marked as read and sent read receipt.")
}

func (a *emulatorApp) listMessages() {
	if err := a.refreshMessages(); err != nil {
		fmt.Println("Failed to refresh statuses:", err)
	}
	if len(a.messages) == 0 {
		fmt.Println("No messages locally. Use refresh.")
		return
	}
	for i, msg := range a.messages {
		direction := "IN "
		peer := msg.From
		if msg.Local || msg.From == a.pin {
			direction = "OUT"
			peer = strings.Join(msg.To, ",")
		}
		fmt.Printf("%2d. [%s] %s %s priority=%d status=%s subject=%q\n", i+1, direction, msg.CreatedAt.Format("15:04:05"), peer, msg.Priority, a.messageStatus(msg), msg.Subject)
	}
}

func (a *emulatorApp) chooseMessage() *emulatorMessage {
	a.listMessages()
	if len(a.messages) == 0 {
		return nil
	}
	raw := a.prompt("Message number: ")
	index, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil || index < 1 || index > len(a.messages) {
		fmt.Println("Invalid number.")
		return nil
	}
	return &a.messages[index-1]
}

func (a *emulatorApp) showMessage() {
	msg := a.chooseMessage()
	if msg == nil {
		return
	}
	fmt.Println()
	fmt.Printf("ID: %d\n", msg.ID)
	fmt.Printf("Od: %s\n", msg.From)
	fmt.Printf("Do: %s\n", strings.Join(msg.To, ","))
	fmt.Printf("Time: %s\n", msg.CreatedAt.Format(time.RFC3339))
	fmt.Printf("Priority: %d\n", msg.Priority)
	fmt.Printf("Status: %s\n", a.messageStatus(*msg))
	fmt.Printf("Subject: %s\n", msg.Subject)
	fmt.Println("Body:")
	fmt.Println(msg.Body)
	fmt.Println()
}

func (a *emulatorApp) sendInteractive() {
	to := a.prompt("Do PIN: ")
	subject := a.prompt("Temat: ")
	priority := a.promptPriority(1)
	body := a.promptMultiline("Body:")
	if strings.TrimSpace(to) == "" {
		fmt.Println("Missing recipient.")
		return
	}
	if err := a.send(to, subject, priority, body); err != nil {
		fmt.Println("Send error:", err)
	}
}

func quoteBody(msg emulatorMessage) string {
	var quoted []string
	for _, line := range strings.Split(msg.Body, "\n") {
		quoted = append(quoted, "> "+line)
	}
	header := fmt.Sprintf("---- Original PIN message ----\nFrom: %s\nSubject: %s\n\n", msg.From, msg.Subject)
	return "\n\n" + header + strings.Join(quoted, "\n")
}

func (a *emulatorApp) replyInteractive() {
	msg := a.chooseMessage()
	if msg == nil {
		return
	}
	to := msg.From
	if msg.Local || msg.From == a.pin {
		if len(msg.To) > 0 {
			to = msg.To[0]
		}
	}
	subject := msg.Subject
	if subject != "" && !strings.HasPrefix(strings.ToLower(subject), "re:") {
		subject = "Re: " + subject
	}
	body := a.promptMultiline("New reply body:")
	body += quoteBody(*msg)
	if err := a.send(to, subject, msg.Priority, body); err != nil {
		fmt.Println("Reply error:", err)
	}
}

func (a *emulatorApp) showThreads() {
	threads := map[string][]emulatorMessage{}
	for _, msg := range a.messages {
		peer := msg.From
		if msg.Local || msg.From == a.pin {
			peer = strings.Join(msg.To, ",")
		}
		subject := strings.TrimSpace(strings.TrimPrefix(strings.TrimPrefix(msg.Subject, "Re:"), "RE:"))
		key := peer + " | " + subject
		threads[key] = append(threads[key], msg)
	}
	if len(threads) == 0 {
		fmt.Println("No threads.")
		return
	}
	keys := make([]string, 0, len(threads))
	for key := range threads {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for i, key := range keys {
		items := threads[key]
		sort.Slice(items, func(i, j int) bool { return items[i].CreatedAt.Before(items[j].CreatedAt) })
		last := items[len(items)-1]
		fmt.Printf("%2d. %s, messages=%d, last=%s\n", i+1, key, len(items), last.CreatedAt.Format("15:04:05"))
	}
}

func (a *emulatorApp) run() {
	for {
		fmt.Println()
		fmt.Printf("PIN Emulator | PIN %s | Server %s\n", a.pin, a.server)
		fmt.Println("1. Refresh / receive new")
		fmt.Println("2. List messages")
		fmt.Println("3. View message")
		fmt.Println("4. Send message")
		fmt.Println("5. Reply with quote")
		fmt.Println("6. Threads")
		fmt.Println("7. Mark received as read")
		fmt.Println("8. Exit")
		choice := a.prompt("> ")
		switch strings.TrimSpace(choice) {
		case "1":
			if err := a.poll(); err != nil {
				fmt.Println("Poll error:", err)
			}
		case "2":
			a.listMessages()
		case "3":
			a.showMessage()
		case "4":
			a.sendInteractive()
		case "5":
			a.replyInteractive()
		case "6":
			a.showThreads()
		case "7":
			a.markReadInteractive()
		case "8", "q", "quit", "exit":
			return
		default:
			fmt.Println("Unknown option.")
		}
	}
}

func main() {
	server := flag.String("server", "http://127.0.0.1:8080", "server address")
	mode := flag.String("mode", "interactive", "interactive, send or poll")
	pin := flag.String("pin", "3D8C2E9A", "emulator PIN")
	to := flag.String("to", "", "recipient PIN for mode=send")
	subject := flag.String("subject", "", "subject for mode=send")
	body := flag.String("body", "", "body for mode=send")
	priority := flag.Int("priority", 1, "priority for mode=send: 0 low, 1 normal, 2 high")
	ack := flag.Bool("ack", true, "mark received messages as read")
	interval := flag.Duration("interval", 2*time.Second, "polling interval; 0 means one-time")
	flag.Parse()

	switch *mode {
	case "interactive", "menu", "tui":
		newApp(*server, *pin).run()
	case "send":
		if *to == "" {
			log.Fatal("missing -to")
		}
		runSend(*server, *pin, *to, *subject, *priority, *body)
	case "poll":
		runPoll(*server, *pin, *ack, *interval)
	default:
		log.Fatalf("unknown mode: %s", *mode)
	}
}
