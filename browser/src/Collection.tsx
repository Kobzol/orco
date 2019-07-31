import React from 'react';
import ReactTable, { CellInfo, Column } from 'react-table';
import { fetchJsonFromServer } from './service';
import { FaHourglassEnd } from 'react-icons/fa';
import { formatSize } from './utils';
import {
    Link
} from 'react-router-dom';
import { ErrorContainer } from './Error';
import { access } from 'fs';
import { type } from 'os';

interface Props {
    match: any,
    err: ErrorContainer
}

interface EntrySummary {
    key: string,
    size: number,
    config: any,
}

interface State {
    data: EntrySummary[],
    columns: Column[],
    loading: boolean,
}

class Collection extends React.Component<Props, State> {

    constructor(props : Props) {
        super(props);
        console.log(props.match);
        this.state = {data: [], columns: [], loading: true}
    }

    _formatEntry = (entry : EntrySummary) => {
        let config = entry.config;
        return JSON.stringify(config);
    };

    _cellConfigItem = (cellInfo : CellInfo) => {
        let v;
        if (typeof cellInfo.value === "string") {
            v = cellInfo.value;
        } else {
            v = JSON.stringify(cellInfo.value);
        }
        return (<span>{v}</span>);
    };

    _cellValueRepr = (cellInfo: CellInfo) => {
        const v = cellInfo.value;
        if (v !== undefined && v !== null) {
            return <span>{v}</span>
        } else {
            return <FaHourglassEnd/>
        }
    }

    _formatSize = (entry : EntrySummary) => formatSize(entry.size);

    componentDidMount() {
        if (this.props.err.isOk) {
            fetchJsonFromServer("entries/" + this.name, null, "GET").then((data) => {
                let cfgColumns = new Set();
                let nonObjectConfig = false;
                for (let e of data) {
                    let config = e.config;
                    if (typeof config != "object") {
                        nonObjectConfig = true;
                    } else {
                        for (let key in config) {
                            if (config.hasOwnProperty(key)) {
                                cfgColumns.add(key);
                            }
                        }
                    }
                }

                let cfgColumnArray = Array.from(cfgColumns);

                const column_defs = (cfgColumnArray.map((e, i) =>
                ({
                    id: "config_" + i,
                    style: {"background": "#fffff0"},
                    headerStyle: {"background": "#ffff90"},
                    Header: e as string,
                    accessor: "config." + e,
                    Cell: this._cellConfigItem
                })));

                if (nonObjectConfig) {
                    column_defs.unshift({
                        id: "config",
                        style: {"background": "#fffff0"},
                        headerStyle: {"background": "#ffff90"},
                        Header: "Config",
                        accessor: "config",
                        Cell: this._cellConfigItem
                    });
                }

                const config_column : Column = {
                    id: "config",
                    Header: "Config",
                    columns: column_defs
                };

                const columns = [config_column,
                    {
                        "Header": "Value",
                        "columns": [ {
                            "style": {"background": "#f0fff0"},
                            headerStyle: {"background": "#90ff90"},
                            Header: "Repr",
                            accessor: "value_repr",
                            Cell: this._cellValueRepr
                        },
                        {
                            id: "size",
                            Header: "Size",
                            accessor: this._formatSize,
                            maxWidth: 100,
                        },
                        {
                            id: "timestamp",
                            Header: "Timestamp",
                            accessor: "created",
                            maxWidth: 200,
                        }
                    ]
                    },
                ]
                console.log(cfgColumns);
                this.setState({
                    data: data,
                    columns: columns,
                    loading: false
                });
            }).catch((error) => {
                console.log(error);
                this.props.err.setFetchError();
            });
        }
    }

    get name() : string {
        return this.props.match.params.name;
    }

    render() {
        return (
            <div>
            <h1>Collection '{this.name}'</h1>
            {<ReactTable data={this.state.data} loading={this.state.loading} columns={this.state.columns}/>}
            </div>
        );
    }
}

export default Collection;
