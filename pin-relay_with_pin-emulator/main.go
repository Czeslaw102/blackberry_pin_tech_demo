package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Message struct {
	ID          int64              `json:"id"`
	ClientRefID string             `json:"client_refid,omitempty"`
	From        string             `json:"from"`
	To          []string           `json:"to"`
	Subject     string             `json:"subject,omitempty"`
	Priority    int                `json:"priority"`
	Body        string             `json:"body"`
	CreatedAt   time.Time          `json:"created_at"`
	ReadBy      []string           `json:"read_by,omitempty"`
	Receipts    map[string]Receipt `json:"receipts,omitempty"`
}

type Receipt struct {
	DeliveredAt *time.Time `json:"delivered_at,omitempty"`
	ReadAt      *time.Time `json:"read_at,omitempty"`
}

type StoreData struct {
	NextID   int64     `json:"next_id"`
	Messages []Message `json:"messages"`
}

type Store struct {
	path string
	mu   sync.Mutex
	data StoreData
}

type SendRequest struct {
	From        string   `json:"from"`
	To          []string `json:"to"`
	Subject     string   `json:"subject"`
	Priority    *int     `json:"priority"`
	Body        string   `json:"body"`
	ClientRefID string   `json:"client_refid"`
}

type AckRequest struct {
	PIN string `json:"pin"`
	ID  int64  `json:"id"`
}

type ReceiptRequest struct {
	PIN  string `json:"pin"`
	ID   int64  `json:"id"`
	Type string `json:"type"`
}

type OutgoingReceipt struct {
	ID          int64              `json:"id"`
	ClientRefID string             `json:"client_refid,omitempty"`
	To          []string           `json:"to"`
	Subject     string             `json:"subject,omitempty"`
	Receipts    map[string]Receipt `json:"receipts,omitempty"`
}

var pinRe = regexp.MustCompile(`[0-9A-Fa-f]{8}`)

func normalizePIN(value string) string {
	matches := pinRe.FindAllString(value, -1)
	if len(matches) == 0 {
		return strings.ToUpper(strings.TrimSpace(value))
	}
	return strings.ToUpper(matches[len(matches)-1])
}

func NewStore(path string) (*Store, error) {
	store := &Store{path: path, data: StoreData{NextID: 1}}
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		return store, store.saveLocked()
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &store.data); err != nil {
			return nil, err
		}
	}
	if store.data.NextID <= 0 {
		store.data.NextID = 1
	}
	store.migrateReceiptsLocked()
	return store, nil
}

func (s *Store) migrateReceiptsLocked() {
	for i := range s.data.Messages {
		if s.data.Messages[i].Receipts == nil {
			s.data.Messages[i].Receipts = map[string]Receipt{}
		}
		for _, pin := range s.data.Messages[i].ReadBy {
			pin = normalizePIN(pin)
			if pin == "" {
				continue
			}
			receipt := s.data.Messages[i].Receipts[pin]
			if receipt.DeliveredAt == nil {
				deliveredAt := s.data.Messages[i].CreatedAt
				receipt.DeliveredAt = &deliveredAt
			}
			s.data.Messages[i].Receipts[pin] = receipt
		}
	}
}

func (s *Store) saveLocked() error {
	raw, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.path, raw, 0600)
}

func (s *Store) Add(req SendRequest) (Message, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	from := normalizePIN(req.From)
	if from == "" {
		return Message{}, errors.New("missing from PIN")
	}
	to := make([]string, 0, len(req.To))
	for _, item := range req.To {
		pin := normalizePIN(item)
		if pin != "" {
			to = append(to, pin)
		}
	}
	if len(to) == 0 {
		return Message{}, errors.New("missing recipient PIN")
	}
	priority := 1
	if req.Priority != nil {
		priority = *req.Priority
	}
	if priority < 0 || priority > 2 {
		priority = 1
	}
	msg := Message{ID: s.data.NextID, ClientRefID: strings.TrimSpace(req.ClientRefID), From: from, To: to, Subject: req.Subject, Priority: priority, Body: req.Body, CreatedAt: time.Now()}
	s.data.NextID++
	s.data.Messages = append(s.data.Messages, msg)
	return msg, s.saveLocked()
}

func (s *Store) Poll(pin string) []Message {
	s.mu.Lock()
	defer s.mu.Unlock()
	pin = normalizePIN(pin)
	result := []Message{}
	for _, msg := range s.data.Messages {
		if !contains(msg.To, pin) || msg.isDeliveredTo(pin) {
			continue
		}
		result = append(result, msg)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (s *Store) Ack(pin string, id int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	pin = normalizePIN(pin)
	for i := range s.data.Messages {
		if s.data.Messages[i].ID == id {
			now := time.Now()
			if s.data.Messages[i].Receipts == nil {
				s.data.Messages[i].Receipts = map[string]Receipt{}
			}
			receipt := s.data.Messages[i].Receipts[pin]
			if receipt.DeliveredAt == nil {
				receipt.DeliveredAt = &now
			}
			s.data.Messages[i].Receipts[pin] = receipt
			return s.saveLocked()
		}
	}
	return errors.New("message not found")
}

func (s *Store) MarkRead(pin string, id int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	pin = normalizePIN(pin)
	now := time.Now()
	for i := range s.data.Messages {
		if s.data.Messages[i].ID == id {
			if !contains(s.data.Messages[i].ReadBy, pin) {
				s.data.Messages[i].ReadBy = append(s.data.Messages[i].ReadBy, pin)
			}
			if s.data.Messages[i].Receipts == nil {
				s.data.Messages[i].Receipts = map[string]Receipt{}
			}
			receipt := s.data.Messages[i].Receipts[pin]
			if receipt.DeliveredAt == nil {
				receipt.DeliveredAt = &now
			}
			if receipt.ReadAt == nil {
				receipt.ReadAt = &now
			}
			s.data.Messages[i].Receipts[pin] = receipt
			return s.saveLocked()
		}
	}
	return errors.New("message not found")
}

func (m Message) isDeliveredTo(pin string) bool {
	pin = normalizePIN(pin)
	if m.Receipts != nil {
		if receipt, ok := m.Receipts[pin]; ok && receipt.DeliveredAt != nil {
			return true
		}
	}
	return contains(m.ReadBy, pin)
}

func (s *Store) All() []Message {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := append([]Message(nil), s.data.Messages...)
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (s *Store) ReceiptsForSender(pin string) []OutgoingReceipt {
	s.mu.Lock()
	defer s.mu.Unlock()
	pin = normalizePIN(pin)
	result := []OutgoingReceipt{}
	for _, msg := range s.data.Messages {
		if normalizePIN(msg.From) != pin {
			continue
		}
		result = append(result, OutgoingReceipt{ID: msg.ID, ClientRefID: msg.ClientRefID, To: append([]string(nil), msg.To...), Subject: msg.Subject, Receipts: msg.Receipts})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (s *Store) ReceiptsForSenderIDs(pin string, ids map[string]bool) []OutgoingReceipt {
	s.mu.Lock()
	defer s.mu.Unlock()
	pin = normalizePIN(pin)
	result := []OutgoingReceipt{}
	for _, msg := range s.data.Messages {
		if normalizePIN(msg.From) != pin || (!ids[strconv.FormatInt(msg.ID, 10)] && (msg.ClientRefID == "" || !ids[msg.ClientRefID])) {
			continue
		}
		result = append(result, OutgoingReceipt{ID: msg.ID, ClientRefID: msg.ClientRefID, To: append([]string(nil), msg.To...), Subject: msg.Subject, Receipts: msg.Receipts})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func parseIDSet(value string) map[string]bool {
	result := map[string]bool{}
	for _, part := range strings.Split(value, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		result[part] = true
	}
	return result
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if normalizePIN(value) == wanted {
			return true
		}
	}
	return false
}

func writeJSON(w http.ResponseWriter, status int, value interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func sendHandler(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		var req SendRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		msg, err := store.Add(req)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		log.Printf("PIN message id=%d client_refid=%s from=%s to=%v subject=%q priority=%d body=%q", msg.ID, msg.ClientRefID, msg.From, msg.To, msg.Subject, msg.Priority, msg.Body)
		writeJSON(w, http.StatusOK, map[string]interface{}{"status": "ok", "id": msg.ID, "client_refid": msg.ClientRefID})
	}
}

func pollHandler(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		pin := r.URL.Query().Get("pin")
		if pin == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing pin"})
			return
		}
		messages := store.Poll(pin)
		log.Printf("PIN poll pin=%s messages=%d", normalizePIN(pin), len(messages))
		writeJSON(w, http.StatusOK, messages)
	}
}

func ackHandler(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		var req AckRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		if err := store.Ack(req.PIN, req.ID); err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
			return
		}
		log.Printf("PIN ack pin=%s id=%d", normalizePIN(req.PIN), req.ID)
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}
}

func receiptHandler(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		var req ReceiptRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
		receiptType := strings.ToLower(strings.TrimSpace(req.Type))
		switch receiptType {
		case "delivered", "":
			if err := store.Ack(req.PIN, req.ID); err != nil {
				writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
				return
			}
		case "read":
			if err := store.MarkRead(req.PIN, req.ID); err != nil {
				writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
				return
			}
		default:
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "unknown receipt type"})
			return
		}
		log.Printf("PIN receipt pin=%s id=%d type=%s", normalizePIN(req.PIN), req.ID, receiptType)
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}
}

func receiptsHandler(store *Store) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		pin := r.URL.Query().Get("pin")
		if pin == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing pin"})
			return
		}
		ids := parseIDSet(r.URL.Query().Get("ids"))
		receipts := store.ReceiptsForSender(pin)
		if len(ids) > 0 {
			receipts = store.ReceiptsForSenderIDs(pin, ids)
		}
		log.Printf("PIN receipts pin=%s count=%d", normalizePIN(pin), len(receipts))
		writeJSON(w, http.StatusOK, receipts)
	}
}

func main() {
	addr := flag.String("addr", ":8080", "HTTP address")
	dbPath := flag.String("db", "pin_messages.json", "JSON database file")
	flag.Parse()

	store, err := NewStore(*dbPath)
	if err != nil {
		log.Fatalf("database error: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/send-pin", sendHandler(store))
	mux.HandleFunc("/poll", pollHandler(store))
	mux.HandleFunc("/ack", ackHandler(store))
	mux.HandleFunc("/receipt", receiptHandler(store))
	mux.HandleFunc("/receipts", receiptsHandler(store))
	mux.HandleFunc("/messages", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, store.All())
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "PIN relay OK\nPOST /send-pin\nGET /poll?pin=XXXXXXXX\nPOST /ack\nPOST /receipt\nGET /receipts?pin=XXXXXXXX\nGET /messages\n")
	})

	log.Printf("PIN relay listening on %s, db=%s", *addr, *dbPath)
	log.Fatal(http.ListenAndServe(*addr, mux))
}
