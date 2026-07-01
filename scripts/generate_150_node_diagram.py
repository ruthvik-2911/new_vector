import xml.etree.ElementTree as ET
import random
import os

def generate_drawio(num_nodes=150, output_path="data/Policies/150_node_workflow.drawio"):
    # Create the root elements
    mxfile = ET.Element("mxfile", host="Electron", modified="2026-07-01T00:00:00.000Z", agent="Antigravity", version="21.6.8", type="device")
    diagram = ET.SubElement(mxfile, "diagram", name="Page-1", id="diag-1")
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel", dx="1000", dy="1000", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth="3000", pageHeight="3000", math="0", shadow="0")
    root = ET.SubElement(mxGraphModel, "root")
    
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    
    # 1. Generate a 2D Grid for coordinates with MASSIVE spacing
    cols = 15
    rows = 10
    grid_points = []
    # Fill grid left-to-right, top-to-bottom
    for c in range(cols):
        for r in range(rows):
            grid_points.append((c, r))
            
    # No random shuffling of the grid, so flow is strictly left-to-right, top-to-bottom
    if len(grid_points) > num_nodes:
        grid_points = grid_points[:num_nodes]
        
    x_spacing = 700  # HUGE horizontal space for curves
    y_spacing = 300  # HUGE vertical space
    
    # New Theme: Cloud Infrastructure & AI Microservices
    words = ["API Gateway", "Load Balancer", "Auth Service", "User DB", "Redis Cache", "Worker Node", 
             "Kafka Queue", "Vector DB", "LLM Orchestrator", "Embeddings", "S3 Bucket", "Data Pipeline", 
             "Telemetry", "Alerts", "Metrics", "Kubernetes", "Docker Config", "CI/CD Pipeline", 
             "GPU Cluster", "Payment Gateway", "Invoice API", "Audit Log", "Backup Service"]
             
    node_styles = [
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        "ellipse;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;",
        "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;",
        "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;fillColor=#d5e8d4;strokeColor=#82b366;"
    ]

    nodes = []
    
    # 2. Generate Nodes
    for i in range(num_nodes):
        node_id = str(i + 2) # IDs start at 2
        col, row = grid_points[i]
        
        # Stagger rows heavily to ensure curves don't cross perfectly horizontally
        y_offset = (col % 2) * 150 + random.randint(-50, 50)
        
        x = col * x_spacing + 100
        y = row * y_spacing + 100 + y_offset
        
        # Text and style
        if i == 0:
            text = "START: Client Request"
            style = "ellipse;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1"
        elif i == num_nodes - 1:
            text = "END: Final Response Return"
            style = "ellipse;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;fontStyle=1"
        else:
            text = f"{random.choice(words)}\n({random.choice(words)})"
            style = random.choice(node_styles)
            
        width = "130"
        height = "70"
        if "rhombus" in style:
            width = "110"
            height = "110"
            
        cell = ET.SubElement(root, "mxCell", id=node_id, value=text, style=style, vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=width, height=height, **{"as": "geometry"})
        
        nodes.append(node_id)

    # 3. Generate Edges (Connections)
    edge_counter = num_nodes + 2
    
    # Use edgeStyle=none so lines draw directly (diagonally) from node to node. 
    # This mathematically guarantees they won't perfectly overlap on grid tracks like orthogonal lines do!
    edge_styles = [
        "edgeStyle=none;endArrow=classic;html=1;strokeWidth=2;strokeColor=#333333;curved=1;",
        "edgeStyle=none;endArrow=classic;html=1;strokeWidth=2;strokeColor=#0066CC;dashed=1;curved=1;",
        "edgeStyle=none;endArrow=classic;html=1;strokeWidth=2;strokeColor=#CC0000;curved=1;"
    ]
    
    # EVERY connection must have a label as requested by the user
    edge_labels = ["Valid", "Invalid", "Timeout", "Retry", "Cache Hit", "Cache Miss", 
                   "Sync", "Async", "Fallback", "Rollback", "DLQ Route", "Circuit Breaker", 
                   "Success", "Failed", "Warning", "Rate Limited"]

    def add_edge(src, tgt):
        nonlocal edge_counter
        estyle = random.choice(edge_styles)
        # NO empty strings. Every route gets a condition!
        elabel = random.choice(edge_labels) 
        
        edge = ET.SubElement(root, "mxCell", id=f"edge_{edge_counter}", value=elabel, style=estyle, edge="1", parent="1", source=src, target=tgt)
        ET.SubElement(edge, "mxGeometry", relative="1", **{"as": "geometry"})
        edge_counter += 1

    # Guarantee a single connected path from Start to End
    for i in range(num_nodes - 1):
        add_edge(nodes[i], nodes[i+1])
        
    # Add extra branching with BACKWARD loops for Fallbacks
    backward_labels = ["Fallback", "Rollback", "Retry", "Failed", "DLQ Route"]
    
    for i in range(num_nodes - 1):
        # 60% chance to sprout 1 extra branch, 15% chance for 2
        extra = 0
        r = random.random()
        if r < 0.15: extra = 2
        elif r < 0.60: extra = 1
            
        for _ in range(extra):
            estyle = random.choice(edge_styles)
            elabel = random.choice(edge_labels)
            
            # If it's a fallback/retry condition, arrow goes BACKWARDS
            if elabel in backward_labels and i > 5:
                # Target a node between the start and current node
                jump_tgt = random.randint(max(0, i - 15), i - 2)
            else:
                # Normal condition, arrow goes FORWARDS
                if i < num_nodes - 16:
                    jump_tgt = i + random.randint(2, 10)
                elif i < num_nodes - 3:
                    jump_tgt = i + random.randint(2, (num_nodes - 1) - i)
                else:
                    continue
                    
            edge = ET.SubElement(root, "mxCell", id=f"edge_{edge_counter}", value=elabel, style=estyle, edge="1", parent="1", source=nodes[i], target=nodes[jump_tgt])
            ET.SubElement(edge, "mxGeometry", relative="1", **{"as": "geometry"})
            edge_counter += 1

    # Write to file
    tree = ET.ElementTree(mxfile)
    ET.indent(tree, space="  ", level=0)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"Successfully generated a complex, scrollable {num_nodes}-node 2D diagram at: {output_path}")

if __name__ == "__main__":
    generate_drawio(150)
