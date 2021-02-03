package main

import (
	"context"
	"github.com/googollee/go-socket.io"
	log "github.com/sirupsen/logrus"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
	"net/http"
	"time"
)

var (
	nodesCollection *mongo.Collection
)

// newContext returns a context with given duration
func newContext(duration time.Duration) context.Context {
	ctx, _ := context.WithTimeout(context.Background(), duration)
	return ctx
}

func dbConnect() {
	// Connect to DB
	ctx := newContext(10 * time.Second)
	client, err := mongo.Connect(ctx, options.Client().ApplyURI("mongodb://localhost:27017"))
	if err != nil {
		log.Fatal(err)
	}

	// Check the connection
	ctx = newContext(10 * time.Second)
	err = client.Ping(ctx, nil)
	if err != nil {
		log.Fatal(err)
	}

	log.Println("Connected to database")

	// Nodes DB collection
	nodesCollection = client.Database("cdnv3").Collection("nodes")
}

func main() {
	// Create a new socket.io server
	server := socketio.NewServer(nil)

	// Listen for socket.io client connections from ECAs
	server.OnConnect("/", func(s socketio.Conn) error {
		s.SetContext("") // TODO: This should store temporary ECA data for the duration of the current connection
		log.Println("ECA connected:", s.ID(), s.RemoteAddr(), s.RemoteHeader())

		ecaAuthKey := s.RemoteHeader().Get("X-Packetframe-Eca-Auth")
		ecaObjectId, err := primitive.ObjectIDFromHex(ecaAuthKey)
		if err != nil {
			log.Warnf("Invalid ECA ObjectId, breaking connection")
			s.Close() // TODO: handle this better, tell the client to shut itself down
		} else {
			log.Println("Finding ECA", ecaObjectId)
		}

		var node bson.M
		ctx := newContext(10 * time.Second)
		if err := nodesCollection.FindOne(ctx, bson.M{"_id": ecaObjectId}).Decode(&node); err != nil {
			if err.Error() == "mongo: no documents in result" {
				log.Warnf("Unable to find ECA")
			} else {
				log.Fatal(err)
			}
		}
		log.Println(node)

		return nil
	})

	server.OnEvent("/", "notice", func(s socketio.Conn, msg string) {
		log.Println("notice:", msg)
		s.Emit("reply", "have "+msg)
	})

	server.OnEvent("/chat", "msg", func(s socketio.Conn, msg string) string {
		s.SetContext(msg)
		return "recv " + msg
	})

	server.OnEvent("/", "bye", func(s socketio.Conn) string {
		last := s.Context().(string)
		s.Emit("bye", last)
		s.Close()
		return last
	})

	server.OnError("/", func(s socketio.Conn, e error) {
		log.Println("meet error:", e)
	})

	server.OnDisconnect("/", func(s socketio.Conn, reason string) {
		log.Println("closed", reason)
	})

	// Connect to database
	dbConnect()

	// start socket.io handler
	log.Println("Starting socket server")
	go server.Serve()
	defer server.Close()
	log.Println("Started socket server")

	// Setup routes
	http.Handle("/socket.io/", server)

	// Start HTTP server
	log.Println("Serving at localhost:8000...")
	log.Fatal(http.ListenAndServe(":8000", nil))
}
