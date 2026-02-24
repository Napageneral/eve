module github.com/Napageneral/eve

go 1.23

require (
	github.com/Napageneral/taskengine v0.0.0
	github.com/google/uuid v1.6.0
	github.com/mattn/go-sqlite3 v1.14.33
	github.com/nexus-project/adapter-sdk-go v0.0.0
	github.com/spf13/cobra v1.10.2
	gopkg.in/yaml.v3 v3.0.1
)

require (
	github.com/inconshreveable/mousetrap v1.1.0 // indirect
	github.com/spf13/pflag v1.0.9 // indirect
)

replace github.com/Napageneral/taskengine => ../taskengine

replace github.com/nexus-project/adapter-sdk-go => ../nexus/nexus-adapter-sdks/nexus-adapter-sdk-go
