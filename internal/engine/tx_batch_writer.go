package engine

import te "github.com/Napageneral/taskengine/engine"

// Re-export TxBatchWriter from taskengine.
type TxBatchWriterConfig = te.TxBatchWriterConfig
type TxBatchWriter = te.TxBatchWriter

var NewTxBatchWriter = te.NewTxBatchWriter
